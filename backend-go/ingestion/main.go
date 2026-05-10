// go/ingestion/main.go
//
// Bulk CSV ingestion worker for PostgreSQL.
// Reads all *.csv files from CSV_DIR, validates rows, and bulk-inserts
// via pgx CopyFrom using a configurable worker pool.
//
// Environment variables
// ─────────────────────
//   DB_URL       PostgreSQL DSN  (default: postgres://app:changeme@postgres:5432/insights)
//   CSV_DIR      Directory of CSV files  (default: /data/csv)
//   WORKERS      Parallel worker count   (default: runtime.GOMAXPROCS(0))
//   BATCH_SIZE   Rows per CopyFrom call  (default: 1000)

package main

import (
	"context"
	"encoding/csv"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/sync/errgroup"
)

// ── Configuration ─────────────────────────────────────────────────────────

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

// ── Row validation ─────────────────────────────────────────────────────────

// validateRow skips rows where the length doesn't match the header or
// any required field (index 0 or 1) is empty.
func validateRow(headers []string, fields []string) bool {
	if len(fields) != len(headers) {
		return false
	}
	if len(fields) > 0 && strings.TrimSpace(fields[0]) == "" {
		return false
	}
	if len(fields) > 1 && strings.TrimSpace(fields[1]) == "" {
		return false
	}
	return true
}

// ── Worker pool ────────────────────────────────────────────────────────────

// ingestFile reads a CSV file and bulk-inserts rows into the named table.
// Returns (totalRowsInserted, error).
func ingestFile(
	ctx context.Context,
	pool *pgxpool.Pool,
	path string,
	table string,
	workers int,
	batchSize int,
) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, fmt.Errorf("open %s: %w", path, err)
	}
	defer func() {
		if cerr := f.Close(); cerr != nil {
			slog.Warn("file close error", "path", path, "error", cerr)
		}
	}()

	reader := csv.NewReader(f)
	reader.TrimLeadingSpace = true

	// Read header row
	headers, err := reader.Read()
	if err != nil {
		return 0, fmt.Errorf("read header from %s: %w", path, err)
	}

	// Convert headers to lowercase, trim spaces
	for i, h := range headers {
		headers[i] = strings.ToLower(strings.TrimSpace(h))
	}

	// Channel for sending batches of rows to workers
	batchCh := make(chan [][]interface{}, workers*2)

	var (
		totalInserted int
		skippedRows   int
	)

	// errgroup manages worker goroutines
	g, ctx := errgroup.WithContext(ctx)

	// Launch workers
	insertedCh := make(chan int, workers)
	for i := 0; i < workers; i++ {
		g.Go(func() error {
			for batch := range batchCh {
				if len(batch) == 0 {
					continue
				}
				rows, err := pool.CopyFrom(
					ctx,
					pgx.Identifier{table},
					headers,
					pgx.CopyFromRows(batch),
				)
				if err != nil {
					// Log but don't abort — other batches may succeed
					slog.Error("CopyFrom failed",
						"table", table,
						"batch_size", len(batch),
						"error", err,
					)
					return err
				}
				insertedCh <- int(rows)
			}
			return nil
		})
	}

	// Main goroutine: read rows, build batches, send to workers
	go func() {
		defer close(batchCh)

		batch := make([][]interface{}, 0, batchSize)
		for {
			record, err := reader.Read()
			if err == io.EOF {
				break
			}
			if err != nil {
				slog.Warn("csv read error", "path", path, "error", err)
				skippedRows++
				continue
			}

			if !validateRow(headers, record) {
				skippedRows++
				continue
			}

			// Convert []string to []interface{}
			row := make([]interface{}, len(record))
			for i, v := range record {
				row[i] = v
			}
			batch = append(batch, row)

			if len(batch) >= batchSize {
				batchCh <- batch
				batch = make([][]interface{}, 0, batchSize)
			}
		}
		// Send any remaining rows
		if len(batch) > 0 {
			batchCh <- batch
		}
	}()

	// Wait for all workers to finish
	if err := g.Wait(); err != nil {
		close(insertedCh)
		return totalInserted, fmt.Errorf("worker error for table %s: %w", table, err)
	}
	close(insertedCh)

	for n := range insertedCh {
		totalInserted += n
	}

	if skippedRows > 0 {
		slog.Warn("rows skipped during validation",
			"table", table,
			"skipped", skippedRows,
		)
	}

	return totalInserted, nil
}

// ── Main ──────────────────────────────────────────────────────────────────

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})))

	dbURL := getEnv("DB_URL", "postgres://app:changeme@postgres:5432/insights")
	csvDir := getEnv("CSV_DIR", "/data/csv")
	workers := getEnvInt("WORKERS", runtime.GOMAXPROCS(0))
	batchSize := getEnvInt("BATCH_SIZE", 1000)

	// Build connection pool
	cfg, err := pgxpool.ParseConfig(dbURL)
	if err != nil {
		slog.Error("failed to parse DB_URL", "error", err)
		os.Exit(1)
	}
	cfg.MaxConns = int32(workers + 2)

	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		slog.Error("failed to create connection pool", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	// Verify connection
	if err := pool.Ping(context.Background()); err != nil {
		slog.Error("database ping failed", "error", err)
		os.Exit(1)
	}
	slog.Info("connected to PostgreSQL",
		"db_url", maskDSN(dbURL),
		"workers", workers,
		"batch_size", batchSize,
	)

	// Read CSV directory
	entries, err := os.ReadDir(csvDir)
	if err != nil {
		slog.Error("failed to read CSV_DIR", "dir", csvDir, "error", err)
		os.Exit(1)
	}

	start := time.Now()
	totalRows := 0

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".csv") {
			continue
		}

		path := filepath.Join(csvDir, entry.Name())
		tableName := strings.TrimSuffix(filepath.Base(path), ".csv")

		// Skip tables that already have data — makes the service safe to re-run.
		var existingCount int64
		if err := pool.QueryRow(
			context.Background(),
			fmt.Sprintf("SELECT COUNT(*) FROM %s", tableName),
		).Scan(&existingCount); err == nil && existingCount > 0 {
			slog.Info("table already populated, skipping",
				"table", tableName, "existing_rows", existingCount)
			totalRows += int(existingCount)
			continue
		}

		slog.Info("ingesting file", "file", entry.Name(), "table", tableName)

		rows, err := ingestFile(context.Background(), pool, path, tableName, workers, batchSize)
		if err != nil {
			slog.Error("ingestion failed", "table", tableName, "error", err)
			continue
		}

		totalRows += rows
		slog.Info("file ingested", "table", tableName, "rows_inserted", rows)
	}

	slog.Info("ingestion complete",
		"total_rows", totalRows,
		"duration_ms", time.Since(start).Milliseconds(),
	)
}

// maskDSN replaces the password in a DSN with *** for safe logging.
func maskDSN(dsn string) string {
	// Simple mask: hide password between :// and @
	if i := strings.Index(dsn, "://"); i != -1 {
		rest := dsn[i+3:]
		if j := strings.Index(rest, "@"); j != -1 {
			creds := rest[:j]
			if k := strings.Index(creds, ":"); k != -1 {
				return dsn[:i+3] + creds[:k+1] + "***" + dsn[i+3+j:]
			}
		}
	}
	return dsn
}
