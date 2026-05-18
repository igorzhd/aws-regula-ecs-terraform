# Python API — Project Overview

## What This Service Does

This is the brain of the document verification platform. When a user uploads a passport or driver's license photo on the frontend, this API receives the image, sends it to the Regula document reader engine for analysis, collects the results, stores them in a database and file storage, and returns a clean structured response back to the frontend.

Think of it as a middleman: the frontend talks to this API, and this API talks to Regula, the database, and S3 storage. The frontend never talks to Regula directly.

---

## How a Single Request Flows Through the System

```
User uploads image on frontend
        |
        v
POST /process  →  python-api receives the image file(s)
        |
        v
regula.py  →  forwards image to Regula container (localhost:8080)
        |
        v
Regula processes the document and returns a large JSON
        |
        v
parser.py  →  extracts only what we need from that JSON:
               - document type (passport? license? which country?)
               - image quality (focus, glare, resolution per page)
               - text fields (name, DOB, document number, etc.)
               - cropped document images (base64 encoded)
        |
        v
storage.py  →  saves cropped images and raw JSON to S3 (or local folder)
        |
        v
models.py  →  saves session record to PostgreSQL database
        |
        v
main.py  →  returns clean JSON response to the frontend
```

---

## File-by-File Explanation

### `requirements.txt`
The shopping list of all external Python packages this project needs.
Python cannot do much on its own — packages add capabilities like running a web server, talking to a database, or making HTTP requests. This file lists everything needed, and a single command (`pip install -r requirements.txt`) installs all of them at once.

---

### `config.py`
**One place for all configuration.**

This file reads environment variables (settings stored outside the code, like database passwords, URLs, and API keys) and makes them available to the rest of the app. Environment variables are used instead of hardcoding values so that the same code works in local development (pointing to a local database) and in production on AWS (pointing to the real RDS and S3) — you just change the variables, not the code.

Example of what it manages:
- `REGULA_URL` — where the Regula container is running (e.g. `http://localhost:8080`)
- `DATABASE_URL` — connection string for PostgreSQL
- `S3_BUCKET_NAME` — which S3 bucket to store files in
- `STORAGE_MODE` — `local` for development, `s3` for production

---

### `models.py`
**Defines the shape of data in the database.**

SQLAlchemy is a library that lets you define database tables as Python classes instead of writing raw SQL. Each class here represents one table. The `Session` class, for example, defines the `sessions` table with all its columns (transaction_id, created_at, text_fields, image_quality, etc.).

When the app saves a processed document result, it creates a `Session` object in Python and SQLAlchemy translates that into an `INSERT` SQL statement automatically.

This file does not create the table itself — that is done by Alembic migrations (see below).

---

### `schemas.py`
**Defines the shape of data going in and out of the API.**

Pydantic is a library for data validation. Schemas are like contracts: they define exactly what the API expects to receive in a request and exactly what it will return in a response.

For example, the `ProcessResponse` schema might say: "the response will always have a `transaction_id` string, an `overall_status` integer, a `doc_type` list, and a `text_fields` object." If any of that is missing or the wrong type, Pydantic raises an error automatically before it ever reaches the frontend.

The difference between `models.py` and `schemas.py` is: models describe the database, schemas describe the API interface. They often look similar but serve different purposes.

---

### `regula.py`
**Handles all communication with the Regula document reader.**

This file contains one main function: take image bytes (the raw file data), build the request payload that Regula expects (base64-encoded image inside a JSON structure with processing parameters), send it to the Regula container via HTTP, and return the raw JSON response.

It is isolated in its own file so that if Regula's API ever changes, or if you want to swap it for a different document reader in the future, you only change this one file.

---

### `parser.py`
**Extracts the useful parts from Regula's large JSON response.**

Regula returns a very large JSON with 21+ containers of data, most of which you don't need. This file knows how to navigate that structure and pull out exactly:

- **Document type** — from result_type 9 (OneCandidate), one entry per page
- **Image quality** — from result_type 30 (ImageQualityCheckList), per page with individual checks (focus, glare, resolution, perspective angle, bounds, portrait)
- **Text fields** — from result_type 36 (Text), for the 6 fields you specified: surname, given names, date of birth, date of issue, date of expiry, document number. Each field includes all sources (MRZ, VISUAL, BARCODE), their individual values and validity, and the cross-comparison result between sources.
- **Document crop images** — from result_type 37 (Images), fieldType 207 only ("Document front side"), one per page. These are the full cropped document images, not portraits or signatures.

The output of this file is clean, structured Python dictionaries ready to be stored in the database or returned to the frontend.

---

### `storage.py`
**Saves files to either local disk or S3.**

This file handles two modes controlled by the `STORAGE_MODE` config variable:

- **Local mode** (development): saves cropped document images and the raw Regula JSON to a local folder on disk. Useful when you don't want to spin up AWS services just to test.
- **S3 mode** (production): uploads the same files to the real S3 bucket using boto3. Returns S3 keys (file paths) that get stored in the database so the frontend can later request presigned download URLs.

The S3 folder structure per session is:
```
s3://your-bucket/sessions/{transaction_id}/
    page_0_crop.jpg
    page_1_crop.jpg
    raw_response.json
```

---

### `main.py`
**The entry point — defines all API routes and ties everything together.**

This is where FastAPI lives. It defines the HTTP endpoints (routes) that the frontend calls, and each route calls the other files in the right order. Think of it as the director: it doesn't do the heavy lifting itself, it just calls regula.py, parser.py, storage.py, and models.py in the right sequence.

Routes this file defines:

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/process` | Receives image(s), runs the full pipeline, returns session result |
| `GET` | `/sessions` | Returns a list of all processed sessions (for history view) |
| `GET` | `/sessions/{id}` | Returns full details of one session |
| `GET` | `/sessions/{id}/download` | Streams the raw Regula JSON as a file download |
| `GET` | `/sessions/{id}/image/{page}` | Returns a presigned URL for the document crop image |
| `GET` | `/health` | Simple health check endpoint (returns `{"status": "ok"}`) |

FastAPI also automatically generates interactive API documentation at `http://localhost:8001/docs` where you can test every endpoint from a browser without needing a frontend at all.

---

### `Dockerfile`
**Instructions for packaging the Python API into a Docker container.**

This file tells Docker: start from a Python base image, install the requirements, copy the app code, and run the server. Once built, the image can run anywhere — locally, on ECS in AWS, or on any other machine — without needing Python or any dependencies installed on the host machine.

---

### `docker-compose.yml`
**Runs the entire local development environment with one command.**

This file defines three services that all start together with `docker compose up`:
- **regula** — the Regula document reader container (already exists)
- **postgres** — a local PostgreSQL database (replaces the real RDS during development)
- **python-api** — the API you are building

All three can talk to each other by service name (e.g. the API reaches Regula at `http://regula:8080` and the database at `postgresql://postgres:5432/docverify`).

---

### `alembic/` folder
**Database migration history.**

Alembic tracks changes to your database schema over time, similar to how Git tracks changes to code. When you add a column or change a table, you create a migration file that describes the change. Alembic can then apply or roll back that change on any database — local, staging, or production — without manual SQL.

You won't touch this folder much manually. The commands `alembic revision --autogenerate` and `alembic upgrade head` handle most of it.

---

## Technology Choices Explained Simply

| Technology | What it is | Why we use it |
|------------|-----------|---------------|
| **FastAPI** | Python web framework | Modern, fast, generates API docs automatically, built for async |
| **SQLAlchemy** | Database toolkit | Write Python instead of raw SQL, works with any database |
| **asyncpg** | PostgreSQL driver | Async version, required for FastAPI's non-blocking architecture |
| **Alembic** | Migration tool | Tracks and applies database schema changes safely |
| **Pydantic** | Data validation | Validates all incoming and outgoing data automatically |
| **httpx** | HTTP client | Async HTTP requests to Regula (async-compatible unlike `requests`) |
| **boto3** | AWS SDK | Official Amazon library for interacting with S3 |
| **uvicorn** | Web server | Runs the FastAPI application, handles incoming connections |
| **python-multipart** | File upload parser | Required by FastAPI to handle multipart form data (image uploads) |
| **python-dotenv** | Env file loader | Loads `.env` file variables into the environment locally |

---

## Local Development vs Production

| Concern | Local Development | Production (AWS) |
|---------|-------------------|-----------------|
| Database | PostgreSQL in Docker | RDS PostgreSQL |
| File storage | Local folder on disk | S3 bucket |
| Regula | Docker container | ECS Fargate task |
| Python API | Docker container | ECS Fargate task |
| Config | `.env` file | AWS Secrets Manager / ECS env vars |

The code is identical in both environments. Only the environment variables change.