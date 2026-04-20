# Multi-Agent Travel Planning Assistant

An intelligent travel itinerary planning system built with **FastAPI**, **LangGraph**, and **Elasticsearch**. This application uses multi-agent orchestration to create personalized travel plans based on user preferences, location data, and real-time information.

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
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
python scripts/preprocess.py    # Unify and process place data from sources
python scripts/build_rag.py     # Build RAG documents with semantic chunking
# Note: If you skip these steps, the system will use pre-built data
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
  "trace": ["intake", "prepare_query", "retrieve_candidates", ...]
}
```

## 🏗️ Project Architecture

### Core Modules

#### `/app/graph/`
- **`build_graph.py`**: Constructs the LangGraph state machine for multi-stage processing
- **`state.py`**: Defines `TravelGraphState` - the shared state across all agents
- **`nodes.py`**: Individual processing nodes (intake, retrieval, answer generation, etc.)

#### `/app/agents/`
- **`intake_agent.py`**: Extracts destination, duration, and interests from user message
- **`context_builder_agent.py`**: Builds context from retrieved places
- **`llm_agent.py`**: Generates answers (with OpenRouter/OpenAI fallback to deterministic responses)
- **`itinerary_builder.py`**: Creates structured day-by-day plans

#### `/app/services/`
- **`rag_service.py`**: RAG orchestration and artifact retrieval
- **`vector_rag.py`**: Vector search and semantic chunking
- **`place_fit_scoring.py`**: Scores places by relevance to user interests
- **`place_metadata.py`**: Enriches place data (geolocation, categories, etc.)
- **`response_formatter.py`**: Formats final responses and follow-up questions

#### `/app/tools/`
- **`elasticsearch_tool.py`**: Elasticsearch place search (primary source)
- **`nominatim_tool.py`**: OpenStreetMap geocoding fallback (free, no API key required)
- **`google_places_tool.py`**: Google Places API integration (optional, enabled via config)
- **`mytomtom_tool.py`**: TomTom routing and distance calculation (optional)
- **`local_catalog_tool.py`**: Local JSON-based place catalog

### Data Flow

```
User Input
    ↓
[intake_agent] → Extract destination, days, interests
    ↓
[prepare_query] → Format RAG query
    ↓
[retrieve_candidates] → Search places (lexical + vector)
    ↓
[context_builder] → Enrich & score places
    ↓
[research] → Generate research context with LLM
    ↓
[itinerary] → Create day-by-day schedule
    ↓
[itinerary_validation] → Validate & retry if needed
    ↓
[source_payload] → Prepare sources & routes
    ↓
[answer] → Generate final response
    ↓
[coordinator] → Coordinate all outputs
    ↓
[planning_response] → Format final response
    ↓
ChatResponse
```

## 🔍 Workflow Stages

### 1. **Intake**
- Extracts destination (Da Nang, Hoi An, Quang Nam)
- Extracts trip duration (1-7 days)
- Identifies interests (museums, beaches, restaurants, temples, etc.)
- Generates follow-up questions if information is incomplete

### 2. **Prepare Query**
- Formats extracted information into RAG queries
- Prepares search parameters for place retrieval

### 3. **Retrieve Candidates**
- Hybrid search combining:
  - **Lexical search**: Elasticsearch keyword matching
  - **Vector search**: Semantic similarity over RAG chunks
- Enriches results with metadata

### 4. **Context Builder**
- Scores places by relevance to user interests
- Filters by availability and fit
- Builds context document for LLM

### 5. **Research**
- Analyzes places and generates insights (LLM-powered with fallback)
- Creates research summary with recommendations

### 6. **Itinerary Building**
- Constructs day-by-day schedule
- Optimizes route and accommodations
- Adds activity timing and notes

### 7. **Validation & Correction**
- Validates itinerary completeness
- Retries with adjusted queries if needed
- Ensures all constraints are met

### 8. **Answer Generation**
- Generates conversational response text
- Formats detailed itinerary
- Provides follow-up suggestions

## 🧠 Hybrid Vector RAG

The system uses hybrid retrieval combining lexical and semantic search:

- **Lexical Search**: Keyword matching via Elasticsearch for precise place names
- **Vector Search**: Semantic similarity using embeddings for contextual matching
- **Chunk-level Indexing**: Documents are split into ~120-word overlapping chunks
- **Fallback Strategy**: If embeddings unavailable, system defaults to lexical-only search

### RAG Pipeline

```bash
# 1. Prepare data
python scripts/preprocess.py
# Output: data/processed/unified_places.json

# 2. Build RAG corpus
python scripts/build_rag.py
# Chunks documents, embeds with multilingual-e5-small
# Output: data/rag/rag_documents.json, rag_documents.jsonl
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
| `coord_coverage_report.py` | Generate coverage statistics |

## 🧪 Testing

Run tests:
```bash
# Test Elasticsearch connectivity
python test_es.py

# Run custom test
python response.py
```

## 📦 Dependencies

- **FastAPI**: Web framework
- **LangGraph**: Multi-agent orchestration
- **Elasticsearch**: Full-text and vector search
- **Pydantic**: Data validation
- **OpenAI/OpenRouter SDK**: Optional LLM integration
- **Sentence Transformers**: For embeddings/RAG

See [requirements.txt](requirements.txt) for full list.

## 📄 License

MIT License - Feel free to use for personal and commercial projects.

## 💡 Features

✅ Multi-stage conversational planning  
✅ Hybrid lexical + vector search  
✅ Automatic interest extraction  
✅ Day-by-day itinerary generation  
✅ Route optimization  
✅ Hotel recommendation  
✅ Fallback strategies for missing data  
✅ Extensible agent architecture  

## 🤝 Contributing

Contributions welcome! Please submit issues and pull requests.
