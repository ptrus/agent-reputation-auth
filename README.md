# SIWE + Agent0 Reputation Server

A Flask server demonstrating Sign-In With Ethereum (SIWE) authentication combined with ERC-8004 Agent0 reputation-based access control.

**Live at:** https://erc8004.ptrus.me/api/gold

## Overview

This server provides a protected endpoint that requires:
1. Valid SIWE (EIP-4361) authentication
2. Agent0 reputation score above a configurable threshold (default: 50)

## Key Features

- **SIWE Authentication**: Standards-compliant Ethereum wallet authentication
- **Reputation-Based Access**: Only high-reputation agents can access protected resources

## Running Locally

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run server
python server.py

# Run tests
pytest test_simple.py test_reputation.py
```

## Docker Deployment

```bash
# Build and run
docker-compose up --build

# The server runs on port 5000
```

## API Endpoints

### GET /api/nonce
Returns a fresh nonce for SIWE authentication.

**Response:**
```json
{"nonce": "32-character-hex-string"}
```

### GET /api/gold
Returns challenge instructions.

### POST /api/gold
Protected endpoint requiring SIWE authentication + reputation.

**Request:**
```json
{
  "message": "SIWE message string",
  "signature": "0x..."
}
```

**Success Response:**
```json
{
  "emoji": "🏆",
  "address": "0x...",
  "agent": {
    "agentId": "...",
    "name": "...",
    "score": 75
  }
}
```

## How It Works

1. **SIWE Message**: Client creates and signs a SIWE message with the nonce
2. **Verification**: Server verifies signature, domain, chain ID, and nonce
3. **Reputation Check**: Server checks if wallet is a high-reputation agent
4. **Access Granted**: Returns protected resource if all checks pass
