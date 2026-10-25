import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Без baseUrl: пути в схеме уже начинаются с /api, а фронт открыт с того же адреса
export const api = createClient<paths>();
