---
name: notion
description: Read and write Notion pages and databases. Use when the user wants to log, store, search, or retrieve information in Notion.
allowed-tools: mcp__notion__notion_search,mcp__notion__notion_get_page,mcp__notion__notion_get_blocks,mcp__notion__notion_create_page,mcp__notion__notion_query_database,mcp__notion__notion_append_blocks,mcp__notion__notion_update_page
---

Use the Notion MCP tools to interact with Notion. Authentication is handled by the host — no API key is needed here.

## Search
`mcp__notion__notion_search` with `query="your search term"`

## Read a page
`mcp__notion__notion_get_page` with `page_id="PAGE_ID"`

## Read page content (blocks)
`mcp__notion__notion_get_blocks` with `page_id="PAGE_ID"`

## Create a page
`mcp__notion__notion_create_page` with `title="Title"`, optionally `body="Content"`
- For a top-level workspace page: omit `parent_page_id`
- To nest under an existing page: include `parent_page_id="PAGE_ID"`

## Query a database
`mcp__notion__notion_query_database` with `database_id="DB_ID"`, optionally `filter={...}`, `sorts=[...]`

## Append blocks to a page
`mcp__notion__notion_append_blocks` with `page_id="PAGE_ID"`, `children=[...]`

## Update a page property
`mcp__notion__notion_update_page` with `page_id="PAGE_ID"`, `properties={...}`
