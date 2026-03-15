/**
 * Notion MCP bridge — runs inside the container as a stdio MCP server.
 *
 * Exposes Notion tools to the agent. Each tool call is forwarded via HTTP
 * to the host-side notion_mcp.py server at NOTION_MCP_URL. The real
 * NOTION_API_KEY never enters the container.
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import http from 'http';

const NOTION_MCP_URL = process.env.NOTION_MCP_URL || 'http://host.docker.internal:3002';

async function callHost(tool: string, input: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ tool, input });
    const parsed = new URL(NOTION_MCP_URL);
    const options: http.RequestOptions = {
      hostname: parsed.hostname,
      port: parseInt(parsed.port || '3002', 10),
      path: '/call',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
    };
    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', (chunk: string) => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse response: ${data}`));
        }
      });
    });
    req.on('error', reject);
    req.setTimeout(35000, () => {
      req.destroy(new Error('Notion host request timed out'));
    });
    req.write(body);
    req.end();
  });
}

const TOOLS = [
  {
    name: 'notion_search',
    description: 'Search Notion pages and databases by keyword',
    inputSchema: {
      type: 'object' as const,
      properties: {
        query: { type: 'string', description: 'Search query' },
      },
      required: ['query'],
    },
  },
  {
    name: 'notion_get_page',
    description: 'Get a Notion page by ID',
    inputSchema: {
      type: 'object' as const,
      properties: {
        page_id: { type: 'string', description: '32-char Notion page ID' },
      },
      required: ['page_id'],
    },
  },
  {
    name: 'notion_get_blocks',
    description: 'Get the block content (children) of a Notion page',
    inputSchema: {
      type: 'object' as const,
      properties: {
        page_id: { type: 'string', description: 'Notion page ID' },
      },
      required: ['page_id'],
    },
  },
  {
    name: 'notion_create_page',
    description: 'Create a new Notion page under a parent page',
    inputSchema: {
      type: 'object' as const,
      properties: {
        parent_page_id: { type: 'string', description: 'ID of the parent page' },
        title: { type: 'string', description: 'Page title' },
        body: { type: 'string', description: 'Optional page body text' },
      },
      required: ['parent_page_id', 'title'],
    },
  },
  {
    name: 'notion_query_database',
    description: 'Query a Notion database with optional filter and sort',
    inputSchema: {
      type: 'object' as const,
      properties: {
        database_id: { type: 'string', description: 'Notion database ID' },
        filter: { type: 'object', description: 'Notion filter object (optional)' },
        sorts: { type: 'array', description: 'Notion sorts array (optional)' },
      },
      required: ['database_id'],
    },
  },
  {
    name: 'notion_append_blocks',
    description: 'Append block content to an existing Notion page',
    inputSchema: {
      type: 'object' as const,
      properties: {
        page_id: { type: 'string', description: 'Notion page ID' },
        children: { type: 'array', description: 'Array of Notion block objects to append' },
      },
      required: ['page_id', 'children'],
    },
  },
  {
    name: 'notion_update_page',
    description: 'Update properties of a Notion page',
    inputSchema: {
      type: 'object' as const,
      properties: {
        page_id: { type: 'string', description: 'Notion page ID' },
        properties: { type: 'object', description: 'Notion properties object to update' },
      },
      required: ['page_id', 'properties'],
    },
  },
];

async function main(): Promise<void> {
  const server = new Server(
    { name: 'notion', version: '1.0.0' },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    try {
      const result = await callHost(name, (args || {}) as Record<string, unknown>);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: 'text' as const,
            text: `Error calling Notion: ${err instanceof Error ? err.message : String(err)}`,
          },
        ],
        isError: true,
      };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  process.stderr.write(`notion-mcp-bridge fatal: ${err}\n`);
  process.exit(1);
});
