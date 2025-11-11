#!/usr/bin/env python3
"""
Simple Flask server demonstrating Sign-In With Ethereum (SIWE) authentication
with Agent0 reputation-based access control.
"""

import os
import secrets
import threading
import time
from datetime import datetime, timezone

from agent0_sdk import SDK
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from siwe import SiweMessage

load_dotenv()

app = Flask(__name__)
CORS(app)

nonces = {}
nonces_lock = threading.Lock()
MAX_NONCES = 10000

reputation_cache = {}
reputation_cache_lock = threading.Lock()
try:
    rpc_url = os.getenv("RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com")
    agent0_sdk = SDK(chainId=11155111, rpcUrl=rpc_url)
    print(f"Agent0 SDK initialized successfully (using {rpc_url})")
except Exception as e:
    print(f"WARNING: Failed to initialize Agent0 SDK: {e}")
    print("Reputation checks will be disabled.")
    agent0_sdk = None


def cleanup_nonces():
    """Background job that cleans up expired nonces every minute."""
    while True:
        try:
            time.sleep(60)
            current_time = datetime.now(timezone.utc)
            with nonces_lock:
                expired = [n for n, t in nonces.items() if (current_time - t).total_seconds() > 300]
                for n in expired:
                    del nonces[n]
                if expired:
                    print(f"Cleaned up {len(expired)} expired nonces")
        except Exception as e:
            print(f"ERROR: Failed to clean up nonces: {e}")


def fetch_all_agents_by_reputation(min_score):
    """Fetch all agents with reputation above min_score, handling pagination."""
    if not agent0_sdk:
        return []

    all_agents = []
    cursor = None
    page = 1

    while True:
        results = agent0_sdk.searchAgentsByReputation(minAverageScore=min_score, cursor=cursor)

        if isinstance(results, dict):
            agents = results.get("items", [])
            cursor = results.get("nextCursor")
        else:
            agents = results
            cursor = None

        all_agents.extend(agents)
        print(f"  Fetched page {page}: {len(agents)} agents")

        if not cursor:
            break
        page += 1

    return all_agents


def fetch_reputation_agents():
    """Background job that fetches agents with reputation >50 every minute."""
    while True:
        try:
            if agent0_sdk:
                min_score = int(os.getenv("MIN_REPUTATION_SCORE", "50"))
                print(f"Fetching agents with reputation >{min_score}...")

                all_agents = fetch_all_agents_by_reputation(min_score)

                with reputation_cache_lock:
                    reputation_cache.clear()
                    for agent in all_agents:
                        wallet_address = getattr(agent, "walletAddress", None)
                        if wallet_address:
                            wallet_address = wallet_address.lower()
                            extras = getattr(agent, "extras", {})
                            score = extras.get("averageScore", 0) if isinstance(extras, dict) else 0

                            reputation_cache[wallet_address] = {
                                "agentId": getattr(agent, "agentId", "N/A"),
                                "name": getattr(agent, "name", "N/A"),
                                "score": score,
                                "fetched_at": datetime.now(timezone.utc).isoformat(),
                            }

                print(f"Updated reputation cache: {len(reputation_cache)} agents")
            else:
                print("WARNING: Agent0 SDK not initialized, skipping reputation fetch")

        except Exception as e:
            print(f"ERROR: Failed to fetch reputation agents: {e}")

        time.sleep(60)


def start_background_jobs():
    """Start background jobs. Should only be called once from main process."""
    nonce_cleanup_thread = threading.Thread(target=cleanup_nonces, daemon=True)
    nonce_cleanup_thread.start()
    print("Started nonce cleanup background job")

    if agent0_sdk:
        reputation_thread = threading.Thread(target=fetch_reputation_agents, daemon=True)
        reputation_thread.start()
        print("Started reputation cache background job")
    else:
        print("WARNING: Reputation checks disabled - Agent0 SDK not available")


def check_reputation(address):
    """Check if an address has reputation above the configured threshold."""
    min_score = int(os.getenv("MIN_REPUTATION_SCORE", "50"))
    if min_score > 999998:
        return True, {"test_mode": True, "note": "Reputation check disabled"}

    if not agent0_sdk:
        test_mode = os.getenv("TEST_MODE", "").lower() == "true"
        if test_mode:
            return True, {"test_mode": True, "note": "Test mode enabled"}
        return False, None

    address_lower = address.lower()
    with reputation_cache_lock:
        agent_info = reputation_cache.get(address_lower)

    if agent_info:
        return True, agent_info
    else:
        return False, None


@app.route("/api/nonce", methods=["GET"])
def get_nonce():
    """Generate and return a new nonce for SIWE authentication."""
    nonce = secrets.token_hex(16)
    current_time = datetime.now(timezone.utc)

    with nonces_lock:
        # Check if we're at capacity
        if len(nonces) >= MAX_NONCES:
            # Clean up expired nonces first
            expired = [n for n, t in nonces.items() if (current_time - t).total_seconds() > 300]
            for n in expired:
                del nonces[n]

            # If still at capacity, remove oldest nonces
            if len(nonces) >= MAX_NONCES:
                # Sort by timestamp and remove oldest 10%
                sorted_nonces = sorted(nonces.items(), key=lambda x: x[1])
                to_remove = int(MAX_NONCES * 0.1)
                for n, _ in sorted_nonces[:to_remove]:
                    del nonces[n]
                print(f"WARNING: Nonce capacity reached, removed {to_remove} oldest nonces")

        # Store nonce with expiration (clean up after 5 minutes)
        nonces[nonce] = current_time

        # Opportunistic cleanup of expired nonces
        expired = [n for n, t in nonces.items() if (current_time - t).total_seconds() > 300]
        for n in expired:
            del nonces[n]

    return jsonify({"nonce": nonce})


def get_instructions(expected_domain, expected_uri, expected_chain_id, expected_statement):
    """Return challenge instructions."""
    min_score = int(os.getenv("MIN_REPUTATION_SCORE", "50"))
    return {
        "message": "Welcome to the ERC-8004 Agent Access Challenge",
        "instructions": {
            "step1": "GET /api/nonce to receive a nonce",
            "step2": "Create SIWE message with the following parameters and sign it with your wallet",
            "required_parameters": {
                "domain": expected_domain,
                "uri": expected_uri,
                "version": "1",
                "chain_id": expected_chain_id,
                "nonce": "obtain from step 1",
                "issued_at": "current timestamp in ISO format",
                "statement": expected_statement,
            },
            "step3": "POST /api/gold with {message, signature}",
            "note": f"Only registered ERC-8004 agents with reputation score >{min_score} are allowed to enter",
        },
    }


@app.route("/api/gold", methods=["GET", "POST"])
def golden_emoji():
    """Protected endpoint requiring SIWE authentication and reputation."""
    expected_domain = os.getenv("EXPECTED_DOMAIN")
    if not expected_domain:
        return jsonify({"error": "Server misconfiguration: EXPECTED_DOMAIN not set"}), 500

    expected_chain_id = int(os.getenv("CHAIN_ID", "999"))
    expected_statement = os.getenv("EXPECTED_STATEMENT", "I want the gold")
    expected_uri = os.getenv("EXPECTED_URI", f"http://{expected_domain}")

    if request.method == "GET":
        return jsonify(
            get_instructions(expected_domain, expected_uri, expected_chain_id, expected_statement)
        ), 200
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid or missing JSON payload"}), 400

        message = data.get("message")
        signature = data.get("signature")

        if not message or not signature:
            error_response = {"error": "Missing message or signature"}
            error_response.update(
                get_instructions(
                    expected_domain, expected_uri, expected_chain_id, expected_statement
                )
            )
            return jsonify(error_response), 400

        siwe_message = SiweMessage.from_message(message=message)

        with nonces_lock:
            if siwe_message.nonce not in nonces:
                return jsonify({"error": "Invalid or expired nonce"}), 401
            del nonces[siwe_message.nonce]

        if siwe_message.domain != expected_domain:
            return jsonify(
                {
                    "error": f"Domain mismatch: expected '{expected_domain}', got '{siwe_message.domain}'"
                }
            ), 401

        if siwe_message.chain_id != expected_chain_id:
            return jsonify(
                {
                    "error": f"Chain ID mismatch: expected {expected_chain_id}, got {siwe_message.chain_id}"
                }
            ), 401

        if siwe_message.statement != expected_statement:
            return jsonify(
                {
                    "error": f"Statement mismatch: expected '{expected_statement}', got '{siwe_message.statement}'"
                }
            ), 401

        if siwe_message.uri != expected_uri:
            return jsonify(
                {"error": f"URI mismatch: expected '{expected_uri}', got '{siwe_message.uri}'"}
            ), 401

        siwe_message.verify(
            signature=signature, domain=expected_domain, timestamp=datetime.now(timezone.utc)
        )

        address = siwe_message.address
        has_reputation, agent_info = check_reputation(address)
        if not has_reputation:
            min_score = int(os.getenv("MIN_REPUTATION_SCORE", "50"))
            return jsonify(
                {
                    "error": f"Access denied: Reputation score must be >{min_score}",
                    "details": "Your wallet address is not associated with a high-reputation agent",
                    "address": address,
                }
            ), 403

        response_data = {"emoji": "🏆", "address": address}
        if agent_info:
            response_data["agent"] = agent_info

        return jsonify(response_data)

    except Exception as e:
        print(f"Verification error: {e}")
        return jsonify({"error": "Verification failed"}), 401


if __name__ == "__main__":
    print("SIWE + Agent0 Reputation Server starting...")
    print("Server available at: http://0.0.0.0:5000")

    start_background_jobs()
    app.run(debug=False, port=5000, host="0.0.0.0")
