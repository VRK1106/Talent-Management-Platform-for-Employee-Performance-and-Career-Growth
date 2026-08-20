# Talent Management Platform for Employee Performance and Career Growth 🚀

> **An enterprise-grade, privacy-aware AI platform for employee training, performance analytics, RAG document search, automated exam/sprint creation, and interactive voice assistance.**

---

## 📌 Project Overview

The **Talent Management Platform for Employee Performance and Career Growth** is a modern Flask MVC web application designed to streamline enterprise workforce development. It unifies multi-role user authentication, real-time trainee performance tracking, RAG-powered document search (ChromaDB + Sentence Transformers), automated AI exam & 6-day Sprint study plan generation, email notifications, and an offline interactive Voice AI Assistant (`Sphere Voice AI`).

---

## ✨ Key Features

- 🔐 **Multi-Role Authentication & Access Control**:
  - Distinct portals for **Trainee**, **Manager**, and **Admin**.
  - User registration, login, profile management, and accommodation requests.
  - Facial recognition descriptor support for biometric user verification.

- 📊 **Interactive Executive Dashboard & Analytics**:
  - Real-time KPIs for active trainees, completed exams, assignees, and sprint progress.
  - Trainee grade breakdowns, score distributions, and performance history.

- 📥 **Document Ingestion & Semantic RAG Engine**:
  - Ingest PDF documents with automatic layout preservation and OCR fallback.
  - BGE-Large (`BAAI/bge-large-en-v1.5`) embeddings via Sentence-Transformers.
  - Persistent vector storage powered by **ChromaDB**.
  - RAG query generation using local Ollama (`qwen2.5-coder`) or Groq API cloud models.

- 🎙️ **Sphere Voice AI Assistant**:
  - Real-time voice query input (Speech-to-Text) with live audio visualizer.
  - Text-to-Speech (TTS) automated answer readout.
  - Dynamic tool calling to fetch trainee metrics, list exams, create training announcements, or trigger exam generation directly via voice.

- 📝 **AI Exam & Sprint Study Plan Generator**:
  - Automatically construct multi-question exams from single or combined PDF reference documents.
  - Generate 6-day Sprint study plans with daily tasks, reference materials, and Day 5 Gateway Exams.
  - Instant grading, score submission, and trainee performance reporting.

- 📢 **Email Notifications Service**:
  - Automated credential delivery for new user registrations.
  - Instant SMTP broadcasts for corporate announcements and assigned exams.

- 🖥️ **System Info & Hardware Diagnostics**:
  - Real-time system monitoring (CPU/GPU utilization, memory, vectorstore status).

---

## 📂 Project Architecture

```
Talent-Management-Platform/
├── app.py                     # Entry point for the Flask MVC web application
├── requirements.txt           # Python package dependencies
├── .env.example               # Environment template configuration file
├── README.md                  # Main setup and documentation guide
├── .gitignore                 # Version control exclusion rules
├── assets/                    # Styling, CSS stylesheets, and images
│   └── styles.css             # Glassmorphism dark/light design system
├── templates/                 # HTML5 Jinja2 view templates
│   ├── base.html              # Core navigation layout and header
│   ├── login.html             # User authentication portal
│   ├── dashboard.html         # Executive performance & KPI dashboard
│   ├── ingest.html            # Document uploading & vector indexing
│   ├── search.html            # Semantic search lab & Qwen/Groq RAG
│   ├── exams.html             # Exam creation, assignment, & grading
│   ├── voice_assistant.html   # Sphere Voice AI interactive interface
│   └── system_info.html       # Hardware diagnostics & system health
├── src/                       # Backend business logic modules
│   ├── config.py              # Application settings, paths, & RAG parameters
│   ├── users.py               # SQLite user database, auth, & face descriptors
│   ├── exams.py               # Exam schema, scoring, & assignment handling
│   ├── sprints.py             # 6-day Sprint study plan generator & manager
│   ├── student_performance.py # Trainee analytics & score aggregations
│   ├── ingest.py              # PDF extraction, OCR fallback, & text chunking
│   ├── embeddings.py          # Sentence-Transformers embedding wrapper
│   ├── vectorstore.py         # ChromaDB index operations, search, & stats
│   ├── llm.py                 # Ollama & Groq API LLM connectors
│   ├── voice_agent.py         # Sphere Voice AI prompt engine & tool execution
│   └── mail.py                # Asynchronous SMTP email dispatching
└── documents/                 # Directory for raw uploaded reference PDFs
```

---

## 🛠️ Prerequisites

1. **Python**: Version **3.10** or higher.
2. **LLM Provider** (Choose one or both):
   - **Local Ollama** (Recommended for 100% offline privacy): Install [Ollama](https://ollama.ai/) and pull `qwen2.5-coder`.
   - **Groq API** (For cloud LLM processing): Obtain an API key from [Groq Console](https://console.groq.com/).
3. **Optional**: Chrome/Edge browser for Web Speech API microphone features.

---

## 🚀 Quick Setup & Installation Guide

### Step 1: Clone the Repository

```bash
git clone https://github.com/VRK1106/Talent-Management-Platform-for-Employee-Performance-and-Career-Growth.git
cd Talent-Management-Platform-for-Employee-Performance-and-Career-Growth
```

### Step 2: Create & Activate Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\activate
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

Create a `.env` file in the root directory by copying `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` to configure your settings:
```env
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
CHROMA_DB_PATH=./chroma_db
CHROMA_COLLECTION=talent_sphere_docs
CHUNK_SIZE=800
CHUNK_OVERLAP=150
TOP_K=5
DOCUMENTS_DIR=./documents
GROQ_API_KEY=your_groq_api_key_here
```

### Step 5: Launch the Application

Run `app.py` to start the Flask server:

```bash
python app.py
```

The application will initialize SQLite databases (`users.db`, `exams.db`) and open at:
👉 **`http://localhost:5000`** (or `http://127.0.0.1:5000`)

---

## 📄 License & Maintainer

Developed for workforce training, performance acceleration, and career elevation.
For questions or support, contact **VRK1106**.
