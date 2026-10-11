# Price-Scrapper Documentation

## Table of Contents

1. [Project Structure](#project-structure)
2. [Setup & Installation](#setup--installation)
3. [Core Components](#core-components)
4. [Adding a New Scrapper](#adding-a-new-scrapper)
5. [API Endpoints](#api-endpoints)
6. [Utilities & Helpers](#utilities--helpers)
7. [Testing & Debugging](#testing--debugging)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)
10. [Git Branch](#git-branch)
11. [Table Structures](#table-structures)
12. [Threshold](#threshold)
13. [Other File Contents](#other-file-contents)

## Project Structure

```
Price-Scrapper/
│
├── api/
│   └── routes.py
│
├── config/
│   ├── __init__.py
│   ├── aws.py
│   ├── email.py
│   ├── scrapper_config.py
│   └── scrapping_tracker_config.py
│
├── fetch_scripts/
│   ├── __init__.py
│   ├── fetch_ebay.py
│   ├── fetch_farfetch.py
│   ├── fetch_fashionphile.py
│   ├── fetch_google.py
│   ├── fetch_rebag.py
│   └── fetch_stockx.py
│
├── models/
│   ├── __init__.py
│   └── product_item.py
│
├── schemas/
│   ├── __init__.py
│   ├── product_item.py
│   ├── productId_input.py
│   └── search_input.py
│
├── scrappers/
│   ├── __init__.py
│   ├── chrono24.py
│   ├── ebay.py
│   ├── farfetch.py
│   ├── fashionphile.py
│   ├── flightclub.py
│   ├── goat.py
│   ├── google_shopping.py
│   ├── grailed.py
│   ├── realreal.py
│   ├── rebag.py
│   ├── serp.py
│   ├── stockx.py
│   ├── vestiaire.py
│   └── watchcharts.py
│
├── utils/
│   ├── __init__.py
│   ├── antropic.py
│   ├── database.py
│   ├── email.py
│   ├── global_lock.py
│   ├── image_search.py
│   ├── logger.py
│   ├── product_engine.py
│   └── utility.py
│
├── logs/
│
├── archive_tables.py
├── main.py
├── README.md
├── requirement.txt
├── run_all_fetchers.py
├── run_engine.py
├── schedule_fetchers.py
├── tables.py
├── test.py
├── ghp_9u7axFfBvNOHiFtyo5owmtn0JEVtaF4.txt
```

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Auricore/Price-Scrapper.git
   cd Price-Scrapper
   ```

2. **Install dependencies**
   ```bash
   Install python3.12
   pip install -r requirements.txt
   ```

3. **Configure AWS credentials**

4. **Set up DynamoDB tables**

5. **Configure API keys:**
   Add your API keys (e.g., Scrapfly, SerpAPI, Anthropic) to config/

6. **Run the batch engine**
   ```bash
   python run_engine.py
   ```

## Core Components

### api/
Contains the API route definitions and logic for handling HTTP requests. This is where endpoints are defined for interacting with the scrapper system.

### config/
Holds configuration files and settings, such as AWS credentials, email settings, and scrapper-specific configurations. These files centralize environment-specific and sensitive information.

### fetch_scripts/
Includes scripts responsible for fetching data from various sources (e.g., eBay, Farfetch, Google, etc.). Each script typically handles the data retrieval logic for a specific platform.

### models/
Defines the core data models used throughout the project, such as representations of product items. These models are often used for database interactions and data validation.

### schemas/
Contains schema definitions for data validation and serialization/deserialization, often used with API endpoints to ensure correct data formats (e.g., input and output schemas).

### scrappers/
Implements the actual web scrapers for different platforms (e.g., eBay, StockX, Grailed, etc.). Each file typically contains the scraping logic for a specific website.

### utils/
Provides utility modules and helper functions used across the project, such as database utilities, logging, email sending, image searching, and other shared logic.

### logs/
A directory intended for storing log files generated during the execution of the application, useful for debugging and monitoring.

## Adding a New Scrapper

1. Create a new file in scrappers/ (e.g., scrappers/newplatform.py).
2. Implement a class with a search() method.
3. Add a fetcher script in fetch_scripts/ if batch fetching is needed.
4. Register the scraper in utils/product_engine.py for API integration.

## API Endpoints

### 1. /insert/ (POST)

**Purpose: Search for products (by text or image) and store results.**

**Sample Request**
```json
POST /insert/
{
  "query": "Nike Air Jordan 1",
  "category": "sneakers",
  "product_id": "optional-id",
  "image_url": "https://example.com/image.jpg"
}
```

**Response**
```json
{
  "time_taken_seconds": 2.13,
  "result_count": 12,
  "results": [
    {
      "title": "Nike Air Jordan 1 Retro",
      "listing_price": "200",
      "currency": "USD",
      "url": "https://ebay.com/...",
      "condition": "New",
      "seller": "trusted_seller",
      "location": "USA",
      "source": "Ebay",
      "..."
    }
  ]
}
```

### 2. /get_product/ (POST)

**Purpose: Retrieve products by product ID or search query, with filtering and sorting.**

**Sample Request**
```json
POST /get_product/
{
  "product_id": "abc123",
  "filters": {
    "min_price": "5000",
    "max_price": "15000"
  },
  "limit": 10,
  "sort_by": "listing_price",
  "sort_order": "asc",
  "group_by": "source"
}
```

**Response**
```json
{
  "results": [
    {
      "product": { ... },
      "score": 98
    }
  ],
  "last_evaluated_key": null,
  "total_count": 1,
  "message": "Products retrieved successfully"
}
```

### 3. /count_by_source/ (GET)

**Purpose: Get product counts grouped by their source/platform.**

**Sample Request:**
```
GET /count_by_source/?product_id=abc123
```

**Response**
```json
{
  "product_source_counts": {
    "472b5d3f410092ff": {
      "ebay": 51,
      "google_shopping": 35,
      "stockx": 5
    },
    "a6ab70c72996bfce": {
      "google_shopping": 21,
      "ebay": 7,
      "rebag": 2,
      "stockx": 2
    }
  }
}
```

### 4. /products_by_condition/ (GET)

**Purpose: Retrieve products filtered by their condition (e.g., new, used).**

**Sample Request:**
```
GET /products_by_condition/?condition=New
```

**Response**
```json
{
  "results": [
    { "product": { ... }, "score": 100 }
  ]
}
```

### 5. /missing-products

**Purpose: Retrieve products that are not present in ProductsTable but exists in FinalProductsTable**

**Response**
```json
[
  "8db0ee0b5a63bdae",
  "65c03d1bc457938b",
  "7439f5cf09cc3653",
  "..."
]
```

## Utilities & Helpers

The utils folder contains utility modules and helper functions that provide shared functionality across the entire Price-Scrapper project. These utilities are designed to be reusable and modular, supporting various aspects of the application such as database operations, logging, email handling, image searching, and more. Here's a brief overview of each file:

### anthropic.py
Implements the AnthropicRefiner class, which uses the Anthropic Claude API to:
- Refine product search queries using AI, optionally based on image filenames.
- Score product relevance to a search query.
- Extract product size and condition from titles.
- Standardize and extract geographic locations from product data.
- All methods are designed to return concise, AI-generated results for use in product search and metadata enrichment.

### database.py
Provides utility functions for interacting with an AWS DynamoDB table:
- Checks if a product URL is a duplicate for the current day.
- Checks if a product with a given ID already exists.

### email.py
Contains a function to send emails using SMTP:
- Uses configuration from config/email.py.
- Sends an email with a subject and body to a configured recipient.

### global_lock.py
Implements a GlobalLockManager class to coordinate distributed locking using DynamoDB.
- Allows scripts to acquire and release a global lock (e.g., to prevent multiple fetchers from running simultaneously).
- Lock status and expiration are managed in a DynamoDB table.
- Includes methods to check if a lock is active, acquire a lock, and release it (optionally recording execution time).

### image_search.py
Defines an ImageSearcher class for reverse image search using SerpAPI.
- Sends an image URL to SerpAPI's Google Reverse Image engine.
- Extracts and returns the most relevant product or object name from the API response.

### logger.py
Provides a get_logger function to configure and retrieve a logger for the application.
- Sets up both console and file logging with a consistent format.
- Log files are named per scraper and rotated daily.
- Ensures only one handler is attached per logger instance.

### product_engine.py
Implements the ProductSearchEngine class, which orchestrates product search across multiple platforms.
- Integrates various scrapers (eBay, StockX, Farfetch, etc.), image search, and query refinement.
- Supports asynchronous search and result aggregation.
- Uses utility modules for logging, image search, and query refinement.
- Prepares and returns combined search results from multiple sources.

## Testing & Debugging

- Manual testing: Use tools like Postman or curl to hit API endpoints.
- Logs: Check the logs/ directory for daily log files.
- Error handling: All major errors are logged with stack traces.

## Best Practices

- Use async/await for all network and I/O operations.
- Limit concurrency in each scrapper to avoid rate-limiting or bans.
- Normalize all product data before storing.
- Log only important events and errors to keep logs clean.
- Keep API keys and credentials secure.

## Troubleshooting

- **No results from a scrapper:**
  Check API keys, site structure changes, or rate limits.
- **DynamoDB errors:**
  Ensure tables and indexes exist and credentials are correct.
- **Slow performance:**
  Tune concurrency settings and check for blocking code in scrappers.
- **API errors:**
  Check logs for stack traces and error messages.

## Git Branch

### main
It contains the code with fetching product details page, which follows one product for all scrappers method

### optimized
It contains the code without anti scrapping protection, render_js, and reduced timed out. It follows one scrapper for all the products

## Table Structures

### ProductsTable

| Field | Type | Description |
|-------|------|-------------|
| Id | String | Primary Key (URL \| DATE) |
| url | string | url |
| product_id | string | Product ID (GSI for fast lookup) |
| title | string | Product title |
| listing_price | number | Price |
| currency | string | Currency (default: USD) |
| condition | string | Product condition |
| seller | string | Seller/platform |
| location | string | Product/seller location |
| top_rated | bool | Top-rated flag |
| source | string | Source marketplace |
| size | string | Product size |
| category | string | Product category |
| created_at | string | ISO timestamp |

### ScraperRunTracker

| Field | Type | Description |
|-------|------|-------------|
| product_id | String | Primary key |
| source | String | Contains the source type |
| last_run | Timestamp | Time stamp of last time script runnned |
| next_run | Timestamp | Stores the timestamp for the script to be runned in future |

### ArchiveTable

| Field | Type | Description |
|-------|------|-------------|
| product_id | string | Primary key (unique product id) |
| Date | Dict | Contains avg prices of sources |

### FetcherLocks

| Field | Type | Description |
|-------|------|-------------|
| **task_id** | **String** | **It is primary key for fetcher locks** |
| **expires_at** | **String (Timestamp)** | **It stores timestamps of expiration time** |
| **locked_at** | **String(timestamp)** | **It store the started time for the script** |
| **script_name** | **String(timestamp)** | **It stores the script name** |
| **status** | **String** | **Stores the status for the script** |
| **time_taken** | **Decimal** | **Stores the time taken** |

### ScraperStats

| Field | Type | Description |
|-------|------|-------------|
| **Source** | **String** | **Primary key** |
| **run_id** | **String** | **It is unique id for each run it is secondary key** |
| **products_ran** | **Decimal** | **Stores the number of products ran** |
| **time_taken** | **Decimal** | **Stores the time taken by run** |
| **timestamp** | **String** | **Stores the timestamp** |
| **total_active** | **Decimal** | **Stores the total active items fetched, used for ebay** |
| **total_sold** | **Decimal** | **Stores the total sold products fetched, used for ebay** |
| **total_results** | **Decimal** | **Stores total products fetched** |

## Threshold

### Threshold Limit

`combined_similarity(title: str, query: str, threshold: float = 0.75) -> bool`

This function measures how similar a product title is to a search query by combining two approaches:
- Fuzzy string matching (using RapidFuzz's WRatio)
- Semantic similarity (using Sentence Transformers embeddings)

It computes a weighted average of the fuzzy and semantic scores, and returns True if the combined score meets or exceeds the specified threshold (default: 0.75). This helps determine if a product title is a good match for a user's search, even if the wording is different.

| Source | Limit |
|--------|-------|
| **Chrono24** | **0.75** |
| **Ebay** | **0.8** |
| **FarFetch** | **0.84** |
| **Fashionphile** | **0.6** |
| **Google Shopping** | **0.75** |
| **Grailed** | **0.75** |
| **Real Real** | **0.8** |
| **Rebag** | **0.7** |
| **StockX (fetching top 5 products)** | **0.7** |
| **Vestiaire** | **0.7** |
| **WatchCharts** | **0.7** |

## Other File Contents

### fetch_scripts/

Each fetcher script (eBay, Farfetch, Fashionphile, Google, Rebag, StockX) follows a similar workflow and performs the following grouped operations:

1. **Fetching Product Items**
   - Retrieves product items from a DynamoDB table, often paginating through all available items.
   - Optionally accepts a custom list of products to process.

2. **Asynchronous Processing with Concurrency Control**
   - Uses Python's asyncio and semaphores to process multiple products in parallel, controlling the number of concurrent tasks for efficiency and API rate limiting.

3. **Running Platform-Specific Searches**
   - For each product, invokes the corresponding scraper (e.g., EbayScraper, FarfetchScraper) to fetch active and sold listings or relevant product data from the target marketplace.

4. **Updating Run Trackers**
   - After processing each product, updates a run tracker table in DynamoDB.
   - Records when the product was last scraped and schedules the next run based on configurable intervals.

5. **Updating Statistics**
   - After all products are processed, aggregates results (e.g., total active/sold listings, total products processed, total time taken).
   - Writes a summary of the run to a statistics table in DynamoDB for monitoring and analytics.

6. **Logging**
   - Uses a custom logger to record progress, results, errors, and timing information for each step of the process.

| Operation | Description |
|-----------|-------------|
| Fetch Items | Retrieve products from DynamoDB |
| Async Processing | Use asyncio and semaphores for concurrency |
| Scrape Marketplace | Use platform-specific scraper for data fetching |
| Update Run Tracker | Record last/next run for each product in DynamoDB |
| Update Stats | Aggregate and store run statistics in DynamoDB |
| Logging | Log all actions, results, and errors |

### scrappers/

Each scraper class is responsible for extracting product data from a specific marketplace. They share a common structure and set of operations:

1. **Building and Sending Requests**
   - Construct search URLs based on product queries and categories.
   - Use Scrapfly (or similar) to fetch HTML or JSON data from the target site, often with retries and concurrency control.

2. **Parsing and Extracting Data**
   - Parse the fetched content using BeautifulSoup or JSON parsing.
   - Extract relevant product details: title, price, URL, image, condition, seller, location, and more.
   - Use utility functions for data cleaning, deduplication, and similarity checks.

3. **Product Model Construction**
   - Build a standardized product dictionary or model for each item found.
   - Optionally enrich with additional metadata (e.g., size, location, condition).

4. **Bulk Insertion**
   - Insert or update product data in DynamoDB tables in batches, using thread pools for efficiency.

5. **Logging and Error Handling**
   - Log progress, results, and errors at each step for monitoring and debugging.
   - Handle exceptions gracefully, skipping problematic items but continuing the scrape.

### config/

This folder contains configuration files that centralize and manage key settings, credentials, and constants used throughout the Price-Scrapper project. Each file defines a class with static variables for easy import and use across the codebase.

#### aws.py
Defines the AwsConfig class, which holds AWS and DynamoDB configuration:
- AWS region, access keys, and username (placeholders for credentials).
- Table names for products, tracking, archiving, locking, and statistics.

#### email.py
Defines the EmailConfig class for email settings:
- SMTP host and port.
- Email user, password, and recipient address for sending notifications or alerts.

#### scrapper_config.py
Defines the Config class for API keys and default values:
- API keys for SerpAPI, Scrapfly, and Gemini (used for scraping and AI tasks).
- Default image URL for use when no product image is available.

#### scrapping_tracker_config.py
Defines the ScrappingTrakcerConfig class for scheduling:
- Specifies how often (in hours) each marketplace should be scraped (e.g., eBay every 24 hours, Farfetch every 72 hours, etc.).

### archive_tables.py

- Runs daily at 2:00 AM.
- Finds product records in DynamoDB older than 30 days.
- Groups and averages prices by product and source.
- Archives summarized data to a separate archive table.
- Deletes the original old records from the main table.
- Sends an email report with the results.

This process keeps the main product table optimized and preserves historical pricing data for future analysis.

### run_all_fetchers.py

**Purpose:** Runs all product data fetchers (scrapers) for supported platforms concurrently. This script is used to trigger a full data refresh from all sources.

**How it Works:**
- Imports fetcher classes for each platform from fetch_scripts/.
- Instantiates each fetcher with a concurrency setting.
- Uses asyncio.gather to run all fetchers in parallel.
- Prints the total time taken for all fetchers to complete.

### schedule_fetchers.py

**Purpose:** Automates and schedules product data fetching for specific products and sources, based on a schedule stored in DynamoDB. It is designed for continuous, periodic scraping and can be run as a background service.

**How it Works:**
- Uses a Scheduler class to:
  - Check the ScraperRunTracker table for products due for scraping (based on next_run timestamp).
  - Fetch product details from FinalProductsTable.
  - Group products by source and run the appropriate fetcher for each group.
  - Send a summary email after each run.
- The main loop (poll_every_hour) runs the scheduler every hour.

```python
class Scheduler:
    def get_due_products(self):
        # Returns list of (product_id, source) tuples due for scraping

    def get_product_details(self, product_id):
        # Returns product metadata from FinalProductsTable

    async def run_source_group(self, source, products, fetcher_cls):
        # Runs the fetcher for a group of products from the same source

    async def run_due_fetchers(self):
        # Orchestrates the fetching for all due products and sends summary email

async def poll_every_hour():
    scheduler = Scheduler()
    while True:
        await scheduler.run_due_fetchers()
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(poll_every_hour())
```

This will continuously check for products due for scraping and fetch them every hour.

**Features:**
- Dynamically determines which products need to be scraped.
- Groups products by source for efficient fetching.
- Sends a summary email after each run.
- Handles missing products and unsupported sources gracefully.

### tables.py

**Purpose:** Manages DynamoDB tables used by the project. Provides utilities to create, check, and delete tables and their contents.

**How it Works:**
- Functions to create tables if they don't exist:
  - create_table_if_not_exists: Generic table creation.
  - create_archive_table: For archiving old product data.
  - create_product_table: For main product storage, with a GSI on product_id.
- Function to delete all items from a table:
  - delete_all_items_from_table
- The script can be run directly to perform table operations (e.g., delete all items from ProductsTable).

### run_engine.py

It fetches all the product but it is not used. It works on one product for all market places method

## How to run scripts on ec2 session?

### Starting and Exiting

- Start a new session: `tmux` or `tmux new -s session_name`
- Run python file
- Detach from a session: Press `Ctrl+b`, then `d`
- List sessions: `tmux ls`
- Attach to a session: `tmux attach -t session_name`
- Kill a session: `tmux kill-session -t session_name`

---

Thank you for taking the time to review this documentation.

If you have any questions or require further assistance, please don't hesitate to contact us.

We are committed to ensuring a smooth and successful experience with your project.
## Credential configuration and cleanup

Set `ANTHROPIC_API_KEY` in the process environment before using
`AnthropicRefiner`. Initialization fails with a configuration error when it is
missing. `.env.example` lists the variable without a credential; this module does
not automatically load an environment file. Use the shell or your deployment's
secret manager to supply it.

The October 2026 cleanup removed an embedded Anthropic API key, a GitHub token
artifact, and a private-key file from the current tree. The repository remains
private. Old commits and previously downloaded copies can still contain those
values. The credential owners must revoke or rotate any previously usable token
and replace any deployment identity using the removed private key. These values
were not exercised during cleanup, and deletion is not proof of revocation.
