# Fitness Tracker (Self-Hosted on Raspberry Pi)

## Goal
Build a lightweight fitness tracking app that runs on a Raspberry Pi and is accessible securely via Tailscale.

## Core Features (MVP)
- User authentication (JWT-based)
- Log workouts (exercise, sets, reps, weight)
- Track body metrics (weight, calories)
- View recent workouts

## Tech Stack
- Backend: FastAPI (Python)
- Database: SQLite
- Frontend: Minimal (HTMX or simple React)
- Deployment: Docker + Docker Compose
- Access: Tailscale (no public exposure)

## Constraints
- Must run on low-resource hardware (Raspberry Pi)
- Avoid microservices (single backend service)
- Keep dependencies minimal
- API-first design

## Non-Goals (for now)
- No complex ML features
- No third-party integrations
- No public hosting