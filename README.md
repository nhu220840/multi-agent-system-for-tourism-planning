# Multi-Agent Travel Planning Assistant

An intelligent travel itinerary planning system built with **FastAPI**, **LangGraph**, **PostgreSQL**, and **Elasticsearch**. PostgreSQL is the source of truth for places, sessions, conversations, plans, and RAG chunks; Elasticsearch is a synced search index used for fast lexical retrieval.

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- PostgreSQL
- Elasticsearch (for place search)
- API keys (optional):
  - **OpenRouter** or **OpenAI**: For enhanced LLM features (research, answer generation)
  - Without API keys, the system uses deterministic fallback responses

### Setup & Run

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

### 2. **Prepare data (optional but recommended):**
```bash
python scripts/preprocess.py    # Unify place data and upsert into PostgreSQL
python scripts/build_rag.py     # Build RAG chunks and sync them into PostgreSQL
python scripts/ingest_to_es.py  # Sync PostgreSQL places into Elasticsearch
```

3. **Start the backend:**
```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000` with documentation at `/docs`

## 📋 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/chat` | POST | Create travel itinerary |
| `/docs` | GET | Interactive API documentation (Swagger UI) |

### POST `/api/chat` Request Body
```json
{
  "message": "3-day itinerary in Da Nang, interested in museums and beaches"
}
```

### Response Format
```json
{
  "answer": "Generated itinerary text",
  "collected_info": {"destination": "Da Nang", "days": "3", "interests": "museums, beaches"},
  "sources": [...],
  "plan": "Detailed day-by-day plan",
  "route_plan": [...],
  "recommended_hotel": {...},
  "grounding": {...},
  "trace": ["intake_agent", "planning_agent", "validator_agent", "response_service", ...]
}
```

## 🏗️ Project Architecture

### Core Modules

#### `/app/graph/`
- **`build_graph.py`**: Constructs the LangGraph state machine for the 3-agent flow
- **`state.py`**: Defines `TravelGraphState` - the shared state across all agents
- **`nodes.py`**: Agent nodes for intake, planning, validation, and response formatting

#### `/app/agents/`
- **`intake_agent.py`**: Extracts destination, duration, and interests from user message
- **`context_builder_agent.py`**: Context construction logic reused by the Planning Agent
- **`llm_agent.py`**: Grounded answer generation reused by the Planning Agent
- **`itinerary_builder.py`**: Itinerary building logic reused by the Planning Agent

Note:
- The 3 agents in this project are logical nodes in `app/graph/nodes.py`: `intake`, `planning`, and `validator`.
- Files under `/app/agents/` are reusable implementation modules, not a 1-file-per-agent contract.

#### `/app/services/`
- **`planning_tools.py`**: Tool wrappers used internally by the planning agent
- **`place_repository.py`**: PostgreSQL-backed source of truth for places and RAG chunks
- **`rag_service.py`**: RAG orchestration and artifact retrieval
- **`vector_rag.py`**: Vector search and semantic chunking
- **`place_fit_scoring.py`**: Scores places by relevance to user interests
- **`place_metadata.py`**: Enriches place data (geolocation, categories, etc.)
- **`response_formatter.py`**: Formats final responses and follow-up questions

#### `/app/tools/`
- **`elasticsearch_tool.py`**: Elasticsearch place search with PostgreSQL fallback/source catalog
- **`nominatim_tool.py`**: OpenStreetMap geocoding fallback (free, no API key required)
- **`google_places_tool.py`**: Optional compatibility layer for Google enrichment hooks
- **`mytomtom_tool.py`**: TomTom routing and distance calculation (optional)
- **`local_catalog_tool.py`**: Catalog retrieval adapter over PostgreSQL/Elasticsearch

### Data Flow (3-Agent Architecture)

```
User Input
    ↓
┌─────────────────────────────────────────────────┐
│  AGENT 1: Intake Agent                          │
│  Extract: destination, days, interests          │
│  Output: Structured JSON                        │
└─────────────────────┬───────────────────────────┘
                      ↓
┌──────────────────────────────────────────────────────────────────┐
│  AGENT 2: Planning Agent (Orchestrator)                          │
│  Uses internal tools:                                            │
│  ├─ prepare_query_tool                                           │
│  ├─ retrieve_places_tool (PostgreSQL + Elasticsearch + Vector)   │
│  ├─ score_places_tool                                            │
│  ├─ research_tool                                                │
│  └─ build_itinerary_tool                                         │
│  Output: Complete itinerary + grounding                          │
└─────────────────────┬────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────┐
│  AGENT 3: Validator Agent                       │
│  Validate itinerary                             │
│  ├─ Valid → Next                                │
│  └─ Invalid → Feedback Loop (back to Agent 2)   │
└─────────────────────┬───────────────────────────┘
                      ↓
              Response Service
              Format natural language
                      ↓
                ChatResponse
```

## 🔍 3-Agent Workflow Details

### Agent 1: **Intake Agent**
Parses raw user input into structured data
- Extracts destination (Da Nang, Hoi An, Quang Nam)
- Extracts trip duration (1-7 days)
- Identifies interests (museums, beaches, restaurants, temples, etc.)
- Generates follow-up questions if information is incomplete
- **Output**: Structured JSON with destination, duration, interests

### Agent 2: **Planning Agent** (Orchestrator)
Central agent that orchestrates all planning steps using internal tools:

**Tool 1: Prepare Query**
- Formats extracted information into RAG queries

**Tool 2: Retrieve Places**
- Hybrid search combining:
  - **Lexical search**: Elasticsearch keyword matching
  - **Vector search**: Semantic similarity over RAG chunks
- Enriches results with metadata

**Tool 3: Score Places**
- Scores places by relevance to user interests
- Filters by availability and fit
- Builds context document for LLM

**Tool 4: Research & Insights**
- Analyzes places and generates insights (LLM-powered with fallback)
- Creates research summary with recommendations

**Tool 5: Build Itinerary**
- Constructs day-by-day schedule
- Optimizes route and accommodations
- Adds activity timing and notes
- **Output**: Complete itinerary JSON + grounding data

### Agent 3: **Validator Agent**
Validates and improves the planning output
- Validates itinerary completeness and constraint satisfaction
- **If valid**: Passes to response formatting
- **If invalid**: Provides feedback for Planning Agent to revise (loops back)
- Ensures all requirements are met before final response

### Response Formatting (Service)
Converts structured output to user-friendly format
- Generates conversational response text
- Preserves all grounding metadata and sources
- Provides follow-up suggestions and recommendations

## 🧠 Hybrid Vector RAG

The system uses hybrid retrieval combining lexical and semantic search:

- **Source of Truth**: PostgreSQL stores place records and RAG chunks
- **Lexical Search**: Elasticsearch indexes PostgreSQL places for precise place-name lookup
- **Vector Search**: Semantic similarity using embeddings for contextual matching
- **Chunk-level Storage**: Documents are split into ~120-word overlapping chunks and stored in PostgreSQL
- **Fallback Strategy**: If embeddings unavailable, system defaults to lexical-only search

### RAG Pipeline

```bash
# 1. Prepare and store places
python scripts/preprocess.py
# Output: PostgreSQL places + export files in data/processed/

# 2. Build and store RAG chunks
python scripts/build_rag.py
# Output: PostgreSQL place_chunks + export files in data/rag/

# 3. Sync search index
python scripts/ingest_to_es.py
# Output: Elasticsearch index synced from PostgreSQL
```

## 🔧 Configuration

Environment variables in `.env` (all optional):

```env
# LLM API Keys (optional - uses fallback if not set)
OPENROUTER_API_KEY=your_key              # For OpenRouter LLM
OPENAI_API_KEY=your_key                  # Alternative: OpenAI API

# Additional Services (optional)
GOOGLE_MAPS_API_KEY=your_key             # Google Places API
GOOGLE_PLACES_ENRICH_ENABLED=false       # Enable Google Places enrichment
PLACES_RESOLVER_ENABLED=false            # Enable Nominatim caching
MYTOMTOM_API_KEY=your_key                # TomTom routing (optional)

# Core Services
ELASTICSEARCH_URL=http://localhost:9200  # Elasticsearch endpoint
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/travel

# RAG Settings
RAG_CHUNK_SIZE_WORDS=120                 # Chunk size for documents
RAG_CHUNK_OVERLAP_WORDS=24               # Overlap between chunks
RAG_CONTEXT_CHUNKS=6                     # Number of chunks to retrieve

# Default Locations
PLACES_RESOLVER_BASE_URL=https://nominatim.openstreetmap.org
PLACES_RESOLVER_COUNTRY_CODES=vn
```

### Running Without API Keys

The system works without any API keys:
- ✅ Place search via Elasticsearch
- ✅ Interest extraction and intake flow
- ✅ Itinerary generation
- ⚠️ Answer generation and research use deterministic fallbacks

## 📊 Data Structure

### Unified Places Format
```json
{
  "id": "place_123",
  "name": "Marble Mountains",
  "category": "destination",
  "city": "Da Nang",
  "district": "Ngu Hanh Son",
  "address": "...",
  "coordinates": {"lat": 16.02, "lon": 108.25},
  "description": "...",
  "intent_tags": ["nature", "hiking", "photography"],
  "planner_role": "half_day"
}
```

### RAG Chunks
```json
{
  "doc_id": "chunk_123",
  "place_id": "place_123",
  "title": "Marble Mountains",
  "category": "destination",
  "document_text": "Marble Mountains. Type: destination. ...",
  "embedding": [0.123, -0.456, ...],
  "metadata": {...}
}
```

## 🐳 Docker

Build and run with Docker:

```bash
docker-compose -f docker/docker-compose.yml up
```

## 📝 Scripts

| Script | Purpose |
|--------|---------|
| `preprocess.py` | Unify place data from multiple sources |
| `build_rag.py` | Build RAG corpus and embeddings |
| `ingest_to_es.py` | Index data into Elasticsearch |

## 📦 Dependencies

- **FastAPI**: Web framework
- **LangGraph**: 3-agent orchestration with validation loop and tool execution
- **Elasticsearch**: Full-text and vector search
- **Pydantic**: Data validation
- **OpenAI/OpenRouter SDK**: Optional LLM integration
- **Sentence Transformers**: For embeddings/RAG

See [requirements.txt](requirements.txt) for full list.

## 📄 License

MIT License - Feel free to use for personal and commercial projects.

## 💡 Features

✅ Streamlined 3-agent architecture  
✅ Multi-stage planning within Planning Agent  
✅ Hybrid lexical + vector search  
✅ Automatic interest extraction  
✅ Day-by-day itinerary generation  
✅ Route optimization  
✅ Hotel recommendation  
✅ Validation loop with feedback mechanism  
✅ Tool-based modularity for easy maintenance  
✅ Fallback strategies for missing data  

## 🤝 Contributing

Contributions welcome! Please submit issues and pull requests.
