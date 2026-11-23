# Universal AI Video Production Studio

> **Enterprise-grade, multi-channel autonomous video production platform for automated storytelling, educational content, 3D animation, and social shorts.**

---

## 🌟 Overview

**Universal AI Video Production Studio** is a deterministic, modular video engineering platform designed to manage and produce high-quality video content at scale across multiple independent channels.

Key capabilities:
- **Multi-Channel Architecture**: Independent branding, voices, aspect ratios, and export policies per channel.
- **Unified Media Storage (Task 3.9)**: Complete architectural separation between program source code and binary runtime media/databases, ready for local NVMe and S3/R2 cloud storage.
- **Media Asset Management (MAM)**: Centralized media vault with SHA-256 deduplication, atomic two-phase write journals, and automated crash recovery.
- **Audio Engineering Pipeline**: EBU R128 compliance, strict True Peak guarding ($\le -1.0\text{ dBTP}$), dual-pass audio normalization, and sidechain ducking.
- **Multi-Language TTS & Voice Synthesis**: Modular voice integration supporting offline fallback and neural cloud synthesis (including Modern Standard Arabic with approved narrator profiles).
- **Persistent Distributed Task Queue**: Lease-based execution with worker heartbeats, state machines, and checkpoint-level crash recovery.
- **Tamper-Evident Audit Logging**: SQLite Transactional Outbox combined with SHA-256 hash chaining to ensure compliance and prevent unauthorized export.

---

## 🏗️ Architecture & Storage Separation (Task 3.9)

To ensure repository agility and avoid Git bloat, **no generated media, operational databases, or large video renders are committed to Git**.

```
universal-ai-video-studio/ (Git Repository - Source & Templates Only)
├── src/                      # Application source code
│   ├── core/                 # Config, constants, exceptions, logging
│   ├── database/             # SQLite manager, migrations, models, repositories
│   ├── rendering/            # FFmpeg engine, audio mixer, probe service
│   ├── services/             # MAM, licenses, task queue, audit, recovery
│   ├── storage/              # Unified StorageService & StorageProviders (Task 3.9)
│   └── utils/                # Security validators, hashing, file utilities
├── tests/                    # Comprehensive unit and acceptance test suites
├── docs/                     # Architectural Decision Records (ADRs) & specs
├── global_templates/         # Reusable project schemas and branding templates
├── .env.example              # Environment variable template (No secrets)
├── .gitignore                # Production ignore rules excluding media & databases
├── pyproject.toml            # Project packaging & dependency specifications
└── README.md                 # System documentation

---------------------------------------------------------------------------------
STUDIO_DATA_DIR / External Storage (Runtime Only - NEVER committed to Git)
├── workspace/
│   ├── runtime/              # Active SQLite database (studio.db), WAL, outbox
│   ├── temp/                 # Intermediate render fragments & scratch frames
│   └── logs/                 # Persistent structured log files
├── channels/
│   └── <channel_id>/
│       ├── branding/         # Channel logos, watermarks, intro/outro cards
│       ├── media_vault/      # Originals organized by category (audio, image, video)
│       ├── episodes/         # Episode manifests & project specifications
│       ├── exports/          # Master and rendered MP4 deliverables
│       └── audit/            # Append-only hash-chained audit log file
└── backups/                  # Automated snapshots & recovery backups
```

### Storage Configuration via `STUDIO_DATA_DIR`

By setting the environment variable `STUDIO_DATA_DIR`, you can mount the studio's runtime data onto external storage, dedicated fast NVMe drives, or network storage volumes without modifying any code:

```bash
# Example: Store all production media on a dedicated data partition
export STUDIO_DATA_DIR="/mnt/studio_data"
```

If `STUDIO_DATA_DIR` is not explicitly set, the studio safely defaults to local `./workspace` and `./channels` directories while remaining completely ignored by Git.

---

## 🚀 Quickstart & Setup

### Prerequisites
- **Python**: Version 3.11 or higher
- **FFmpeg & FFprobe**: Version 6.0+ (Must be available on system `PATH` or configured via `FFMPEG_BINARY_PATH`)

### 1. Clone & Set Up Virtual Environment

```bash
git clone <your-github-repo-url>/universal-ai-video-studio.git
cd universal-ai-video-studio

# Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -e .
```

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` to configure your environment, directories, and optional API keys:

```ini
# Storage & Runtime Data Separation (Task 3.9)
# STUDIO_DATA_DIR=/mnt/studio_data

# General Settings
STUDIO_ENV=development
STUDIO_LOG_LEVEL=INFO

# AI Provider Credentials (as needed)
GEMINI_API_KEY=your_key_here
```

### 3. Initialize Database & Run Migrations

```bash
python -c "from src.database.connection import get_db_manager; from src.database.migrations import MigrationManager; MigrationManager(get_db_manager()).run_migrations()"
```

### 4. Run Test Automation Suites (Task 3.10)

The project includes a unified test runner with clean environment isolation and pre-flight checks:

```bash
# 1. Run portable test suite (Unit, Integration, SQLite, StorageService, WorkerPool, synthetic FFmpeg render)
# Recommended for clean developer setups and CI environments:
python scripts/run_tests.py --suite portable

# 2. Run local production acceptance tests (verifies Episode 01, 02, and 03 deliverables):
python scripts/run_tests.py --suite production

# 3. Run all tests:
python scripts/run_tests.py --suite all
```

> [!NOTE]
> In clean CI environments where production media files are not checked into Git, production tests automatically skip (`unittest.SkipTest`) without error, while all portable unit, integration, and synthetic rendering tests run and validate 100% offline.

### 5. Launch Studio WebUI (Task 3.15a / 3.15b / 3.16 / 3.17)

Run the full-featured, zero-npm Python local WebUI on port 8050:

```bash
python scripts/run_studio_web.py --port 8050
```

Access the interface in your browser at `http://127.0.0.1:8050`:
- **Dashboard & Channels**: Multi-channel overview, active production stats, and intelligence summary.
- **Storyboard & Prompts**: Multi-take prompt editor, Character Bible, and Google Flow Outbound/Inbound integration.
- **Render Queue & Batches**: Visual job dispatching, multi-worker queue management, and batch operations.
- **Video Player**: HTTP Range streaming (206 Partial Content) with zero-leak handle management.
- **Export & Publishing**: Multi-platform profiles, Arabic thumbnail generator, and license preflight.
- **Production Intelligence (`/intelligence`)**: Resource consumption footprints, deterministic insights, flow credit ledger, cost tracking, and CSV/JSON reporting.

### 6. Production Intelligence CLI (Task 3.17)

Comprehensive CLI for resource auditing, cost analytics, and credit logging:

```bash
# Show channel intelligence summary
python -m src.cli.intelligence summary --channel ch_dreammatter_001

# Export intelligence reports (JSON / CSV)
python -m src.cli.intelligence export --channel ch_dreammatter_001 --format json --output report.json
python -m src.cli.intelligence export --channel ch_dreammatter_001 --format csv --output report.csv

# Log manual Google Flow credit transactions
python -m src.cli.intelligence credit log --channel ch_dreammatter_001 --amount 50 --type TOPUP --desc "Monthly refill"

# Configure production unit cost rates
python -m src.cli.intelligence cost set --channel ch_dreammatter_001 --unit-scene 0.15 --unit-min 0.50 --currency USD
```

---

## 🔒 Security & Compliance

- **No Secrets in Repo**: All credentials and API keys are strictly externalized via `.env`.
- **Path Traversal Defense**: All storage operations and asset resolutions strictly validate boundaries against directory traversal (`../` or drive escapes).
- **Asset Licensing Gate**: Assets cannot be exported into final delivery packages without an active, verified commercial license record.
- **Tamper-Evident Chain**: SQLite Transactional Outbox and file-based hash chaining prevent audit suppression or post-render alterations.

---

## 📄 License & Attribution

Copyright © 2026 Universal AI Video Production Studio. All rights reserved.

