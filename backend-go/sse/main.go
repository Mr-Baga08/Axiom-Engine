// go/sse/main.go
//
// SSE Gateway — streams LLM tokens from the Python AI service to browsers.
//
// Features
// ────────
//   • SSE streaming with per-chunk flush
//   • 15-second keep-alive comment pings
//   • Per-IP Redis token-bucket rate limiting  (10 req/min default)
//   • Circuit breaker  (3 consecutive 500s in 30 s → 503 for 60 s)
//   • Graceful shutdown on SIGTERM / SIGINT
//   • Structured JSON logging
//   • /health and /metrics endpoints (atomic request/error counters)
//
// Environment variables
// ─────────────────────
//   AI_SERVICE_URL        Python API base URL   (default: http://api:8000)
//   REDIS_URL             redis://host:port/db  (default: redis://redis:6379/0)
//   PORT                  Listen port           (default: 8080)
//   RATE_LIMIT_RPM        Requests/min per IP   (default: 10)
//   CB_THRESHOLD          Circuit-breaker trips  (default: 3)
//   CB_WINDOW_SECONDS     CB observation window  (default: 30)
//   CB_OPEN_SECONDS       CB open duration       (default: 60)

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

// ── Atomic counters ────────────────────────────────────────────────────────

var (
	requestsTotal atomic.Int64
	errorsTotal   atomic.Int64
)

// ── Configuration ─────────────────────────────────────────────────────────

type Config struct {
	Port         string
	AIServiceURL string
	RedisURL     string
	RateLimitRPM int
	CBThreshold  int
	CBWindowSecs int
	CBOpenSecs   int
}

func loadConfig() Config {
	return Config{
		Port:         getEnv("PORT", "8080"),
		AIServiceURL: getEnv("AI_SERVICE_URL", "http://api:8000"),
		RedisURL:     getEnv("REDIS_URL", "redis://redis:6379/0"),
		RateLimitRPM: getEnvInt("RATE_LIMIT_RPM", 10),
		CBThreshold:  getEnvInt("CB_THRESHOLD", 3),
		CBWindowSecs: getEnvInt("CB_WINDOW_SECONDS", 30),
		CBOpenSecs:   getEnvInt("CB_OPEN_SECONDS", 60),
	}
}

// ── Circuit Breaker ────────────────────────────────────────────────────────

type CBState int

const (
	CBClosed   CBState = iota // normal operation
	CBOpen                    // failing — reject requests
	CBHalfOpen                // probing — allow one request
)

type CircuitBreaker struct {
	mu           sync.Mutex
	state        CBState
	failures     int
	threshold    int
	windowStart  time.Time
	windowSecs   time.Duration
	openUntil    time.Time
	openDuration time.Duration
}

func NewCircuitBreaker(threshold, windowSecs, openSecs int) *CircuitBreaker {
	return &CircuitBreaker{
		state:        CBClosed,
		threshold:    threshold,
		windowSecs:   time.Duration(windowSecs) * time.Second,
		openDuration: time.Duration(openSecs) * time.Second,
		windowStart:  time.Now(),
	}
}

// Allow returns true if the request should proceed.
func (cb *CircuitBreaker) Allow() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	now := time.Now()

	switch cb.state {
	case CBOpen:
		if now.After(cb.openUntil) {
			cb.state = CBHalfOpen
			return true
		}
		return false

	case CBHalfOpen:
		return true // one probe request

	default: // CBClosed
		return true
	}
}

// RecordSuccess resets the breaker.
func (cb *CircuitBreaker) RecordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.failures = 0
	cb.state = CBClosed
	cb.windowStart = time.Now()
}

// RecordFailure increments error count; opens breaker if threshold exceeded.
func (cb *CircuitBreaker) RecordFailure() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	now := time.Now()

	// Reset window if expired
	if now.Sub(cb.windowStart) > cb.windowSecs {
		cb.failures = 0
		cb.windowStart = now
	}

	cb.failures++
	if cb.failures >= cb.threshold {
		cb.state = CBOpen
		cb.openUntil = now.Add(cb.openDuration)
		slog.Warn("circuit breaker opened",
			"failures", cb.failures,
			"open_until", cb.openUntil.Format(time.RFC3339),
		)
	}
}

func (cb *CircuitBreaker) State() CBState {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.state
}

func (cb *CircuitBreaker) StateString() string {
	return map[CBState]string{
		CBClosed: "closed", CBOpen: "open", CBHalfOpen: "half-open",
	}[cb.State()]
}

// ── Rate Limiter (Redis token bucket) ────────────────────────────────────

type RateLimiter struct {
	rdb      *redis.Client
	rpmLimit int
}

func NewRateLimiter(redisURL string, rpmLimit int) *RateLimiter {
	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		slog.Warn("redis url parse failed, rate limiting disabled", "error", err)
		return &RateLimiter{rpmLimit: rpmLimit}
	}
	return &RateLimiter{
		rdb:      redis.NewClient(opt),
		rpmLimit: rpmLimit,
	}
}

// Allow returns true if the IP is within its rate limit.
// Uses a sliding-window counter in Redis (1-minute window).
func (rl *RateLimiter) Allow(ctx context.Context, ip string) bool {
	if rl.rdb == nil {
		return true // Redis unavailable — allow all
	}

	key := fmt.Sprintf("sse:ratelimit:%s", ip)
	pipe := rl.rdb.Pipeline()
	incr := pipe.Incr(ctx, key)
	pipe.Expire(ctx, key, 60*time.Second)

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Warn("redis rate limit check failed", "error", err)
		return true // fail open
	}

	count := incr.Val()
	return count <= int64(rl.rpmLimit)
}

// ── SSE Handler ───────────────────────────────────────────────────────────

type SSEHandler struct {
	cfg    Config
	cb     *CircuitBreaker
	rl     *RateLimiter
	client *http.Client
}

func NewSSEHandler(cfg Config, cb *CircuitBreaker, rl *RateLimiter) *SSEHandler {
	return &SSEHandler{
		cfg: cfg,
		cb:  cb,
		rl:  rl,
		client: &http.Client{
			Timeout: 120 * time.Second, // long — LLM can be slow
		},
	}
}

func (h *SSEHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	ip := realIP(r)

	requestsTotal.Add(1)

	// ── Rate limit ────────────────────────────────────────────────────
	if !h.rl.Allow(r.Context(), ip) {
		slog.Warn("rate limit exceeded", "ip", ip)
		errorsTotal.Add(1)
		http.Error(w, `{"error":"rate limit exceeded"}`, http.StatusTooManyRequests)
		return
	}

	// ── Circuit breaker ───────────────────────────────────────────────
	if !h.cb.Allow() {
		slog.Warn("circuit breaker open", "ip", ip)
		errorsTotal.Add(1)
		http.Error(w, `{"error":"service unavailable"}`, http.StatusServiceUnavailable)
		return
	}

	// ── SSE headers ───────────────────────────────────────────────────
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no") // nginx: disable buffering
	w.Header().Set("Access-Control-Allow-Origin", "*")

	flusher, ok := w.(http.Flusher)
	if !ok {
		errorsTotal.Add(1)
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// ── Forward to Python API ─────────────────────────────────────────
	upstream := h.cfg.AIServiceURL + "/internal/query"
	proxyReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, upstream, r.Body)
	if err != nil {
		slog.Error("proxy request create failed", "error", err)
		h.cb.RecordFailure()
		errorsTotal.Add(1)
		writeSSEError(w, flusher, "upstream request failed")
		return
	}

	// Forward auth headers
	for _, hdr := range []string{"Authorization", "Cookie", "X-Session-Id"} {
		if v := r.Header.Get(hdr); v != "" {
			proxyReq.Header.Set(hdr, v)
		}
	}
	proxyReq.Header.Set("Content-Type", "application/json")
	proxyReq.Header.Set("Accept", "text/event-stream")

	resp, err := h.client.Do(proxyReq)
	if err != nil {
		slog.Error("upstream request failed", "error", err)
		h.cb.RecordFailure()
		errorsTotal.Add(1)
		writeSSEError(w, flusher, "upstream connection failed")
		return
	}
	defer resp.Body.Close()

	// ── Handle upstream errors ────────────────────────────────────────
	if resp.StatusCode >= 500 {
		h.cb.RecordFailure()
		errorsTotal.Add(1)
		slog.Error("upstream 5xx", "status", resp.StatusCode)
		writeSSEError(w, flusher, fmt.Sprintf("upstream error %d", resp.StatusCode))
		return
	}
	h.cb.RecordSuccess()

	ttft := time.Since(start)
	slog.Info("stream started", "ip", ip, "ttft_ms", ttft.Milliseconds())

	// ── Stream tokens + keep-alive ────────────────────────────────────
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	// Keep-alive goroutine: sends SSE comment every 15 seconds
	go func() {
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				fmt.Fprint(w, ": keep-alive\n\n")
				flusher.Flush()
			}
		}
	}()

	// Scan upstream SSE response line by line
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 64*1024), 64*1024)

	for scanner.Scan() {
		select {
		case <-ctx.Done():
			return
		default:
		}

		line := scanner.Text()
		if line == "" {
			// Blank line = end of SSE event
			fmt.Fprint(w, "\n")
		} else if strings.HasPrefix(line, "data:") || strings.HasPrefix(line, "event:") {
			fmt.Fprintf(w, "%s\n", line)
		} else {
			// Forward plain text as data field
			fmt.Fprintf(w, "data: %s\n", line)
		}
		flusher.Flush()
	}

	if err := scanner.Err(); err != nil && ctx.Err() == nil {
		slog.Warn("scanner error", "error", err)
	}

	// Final done event
	fmt.Fprint(w, "event: done\ndata: {}\n\n")
	flusher.Flush()

	slog.Info("stream complete",
		"ip", ip,
		"duration_ms", time.Since(start).Milliseconds(),
	)
}

// ── Helpers ───────────────────────────────────────────────────────────────

func writeSSEError(w http.ResponseWriter, f http.Flusher, msg string) {
	payload, _ := json.Marshal(map[string]string{"error": msg})
	fmt.Fprintf(w, "event: error\ndata: %s\n\n", payload)
	f.Flush()
}

func realIP(r *http.Request) string {
	if ip := r.Header.Get("X-Forwarded-For"); ip != "" {
		return strings.SplitN(ip, ",", 2)[0]
	}
	if ip := r.Header.Get("X-Real-IP"); ip != "" {
		return ip
	}
	return r.RemoteAddr
}

// ── Health & Metrics ──────────────────────────────────────────────────────

type Server struct {
	handler *SSEHandler
	cb      *CircuitBreaker
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"status":          "ok",
		"circuit_breaker": s.cb.StateString(),
	}); err != nil {
		slog.Error("health encode error", "error", err)
	}
}

func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"requests_total": requestsTotal.Load(),
		"errors_total":   errorsTotal.Load(),
		"cb_state":       s.cb.StateString(),
	}); err != nil {
		slog.Error("metrics encode error", "error", err)
	}
}

// ── Main ──────────────────────────────────────────────────────────────────

func main() {
	// JSON structured logging
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})))

	cfg := loadConfig()

	cb := NewCircuitBreaker(cfg.CBThreshold, cfg.CBWindowSecs, cfg.CBOpenSecs)
	rl := NewRateLimiter(cfg.RedisURL, cfg.RateLimitRPM)

	sseHandler := NewSSEHandler(cfg, cb, rl)
	srv := &Server{handler: sseHandler, cb: cb}

	mux := http.NewServeMux()
	mux.HandleFunc("/stream", sseHandler.ServeHTTP)
	mux.HandleFunc("/health", srv.health)
	mux.HandleFunc("/metrics", srv.metrics)

	httpSrv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		WriteTimeout:      0,              // SSE: no write timeout
		IdleTimeout:       120 * time.Second,
	}

	// Graceful shutdown
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)

	go func() {
		slog.Info("SSE gateway listening",
			"port", cfg.Port,
			"ai_service", cfg.AIServiceURL,
			"rate_limit_rpm", cfg.RateLimitRPM,
		)
		if err := httpSrv.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	<-stop
	slog.Info("shutting down SSE gateway")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(ctx); err != nil {
		slog.Error("shutdown error", "error", err)
	}
}

// ── Env helpers ───────────────────────────────────────────────────────────

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
