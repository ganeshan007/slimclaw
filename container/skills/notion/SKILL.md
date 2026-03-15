---
name: notion
description: Read and write Notion pages and databases. Use when the user wants to log, store, search, or retrieve information in Notion.
allowed-tools: WebFetch
---

Use WebFetch to call the Notion API. The API key is available as `NOTION_API_KEY` in the environment.

**Base URL:** https://api.notion.com/v1

**Required headers on every request:**
- `Authorization: Bearer $NOTION_API_KEY`
- `Notion-Version: 2022-06-28`
- `Content-Type: application/json` (for POST/PATCH)

---

## Search

POST https://api.notion.com/v1/search

```json
{ "query": "your search term" }
```

---

## Read a page

GET https://api.notion.com/v1/pages/{page_id}

---

## Read page content (blocks)

GET https://api.notion.com/v1/blocks/{page_id}/children

---

## Create a page

POST https://api.notion.com/v1/pages

```json
{
  "parent": { "page_id": "PARENT_PAGE_ID" },
  "properties": {
    "title": {
      "title": [{ "text": { "content": "Page Title" } }]
    }
  },
  "children": [
    {
      "object": "block",
      "type": "paragraph",
      "paragraph": {
        "rich_text": [{ "text": { "content": "Body text here." } }]
      }
    }
  ]
}
```

---

## Query a database

POST https://api.notion.com/v1/databases/{database_id}/query

```json
{
  "filter": {
    "property": "Status",
    "select": { "equals": "In Progress" }
  },
  "sorts": [
    { "property": "Created", "direction": "descending" }
  ]
}
```

---

## Append blocks to a page

PATCH https://api.notion.com/v1/blocks/{page_id}/children

```json
{
  "children": [
    {
      "object": "block",
      "type": "paragraph",
      "paragraph": {
        "rich_text": [{ "text": { "content": "New content to append." } }]
      }
    }
  ]
}
```

---

## Update a page property

PATCH https://api.notion.com/v1/pages/{page_id}

```json
{
  "properties": {
    "Status": { "select": { "name": "Done" } }
  }
}
```
