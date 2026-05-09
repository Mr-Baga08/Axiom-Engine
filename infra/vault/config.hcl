# Vault server configuration — file-based storage, suitable for single-node dev/staging.
# For production HA: replace storage block with Consul or Raft.

storage "file" {
  path = "/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true   # TLS terminated by reverse proxy in production
}

ui           = true
api_addr     = "http://vault:8200"
cluster_addr = "http://vault:8201"
disable_mlock = true
