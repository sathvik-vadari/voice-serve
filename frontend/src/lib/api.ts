const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...opts?.headers },
  });
  return res.json() as Promise<T>;
}

// ── Types ────────────────────────────────────────────────────────────────

export interface TicketRequest {
  query: string;
  location: string;
  user_phone: string;
  user_name?: string;
  max_stores?: number;
  test_mode?: boolean;
  test_phone?: string;
}

export interface TicketResponse {
  ticket_id: string;
  status: string;
  message: string;
}

export interface StoreCall {
  id: number;
  store_id: number;
  vapi_call_id: string | null;
  status: string;
  product_available: boolean | null;
  matched_product: string | null;
  price: number | null;
  delivery_available: boolean | null;
  delivery_eta: string | null;
  delivery_mode: string | null;
  delivery_charge: number | null;
  product_match_type: string | null;
  notes: string | null;
  call_analysis: Record<string, unknown> | null;
  store_name: string;
  phone_number: string | null;
  rating: number | null;
  address: string | null;
  transcript: string | null;
}

export interface TicketStatus {
  ticket_id: string;
  status: string;
  query_type?: string;
  created_at?: string;
  updated_at?: string;
  error?: string;
  result?: Record<string, unknown>;
  product?: Record<string, unknown>;
  stores?: Record<string, unknown>[];
  store_calls?: StoreCall[];
  progress?: {
    stores_found: number;
    calls_total: number;
    calls_completed: number;
    calls_in_progress: number;
  };
  web_deals?: {
    search_summary?: string;
    deals: WebDeal[];
    best_deal?: { platform: string; price?: number; reason?: string };
    surprise_finds?: string;
    price_range?: { lowest?: number; highest?: number; avg?: number };
    status?: string;
  };
  delivery?: {
    order_state?: string;
    logistics_partner?: string;
    delivery_price?: number;
    rider_name?: string;
    rider_phone?: string;
    tracking_url?: string;
    prorouting_order_id?: string;
  };
}

export interface OptionItem {
  store_call_id: number;
  store_id: number;
  store_name: string;
  address?: string;
  phone_number?: string;
  rating?: number;
  matched_product?: string;
  price?: number;
  product_match_type?: string;
  delivery_available?: boolean;
  delivery_eta?: string;
  delivery_mode?: string;
  delivery_charge?: number;
  call_summary?: string;
  notes?: string;
}

export interface WebDeal {
  platform: string;
  product_title?: string;
  price?: number;
  original_price?: number;
  discount_percent?: number;
  url?: string;
  offer_details?: string;
  delivery_estimate?: string;
  in_stock?: boolean;
  confidence?: string;
  why_notable?: string;
}

export interface OptionsResponse {
  ticket_id: string;
  product_requested: string;
  customer_specs?: Record<string, unknown>;
  avg_price_online?: number;
  stores_contacted: number;
  calls_connected: number;
  options_found: number;
  options: OptionItem[];
  web_deals?: WebDeal[];
  web_deals_summary?: string;
  web_deals_best?: { platform: string; price?: number; reason?: string };
  web_deals_surprise?: string;
  message: string;
  quick_verdict?: string;
  error?: string;
  status?: string;
}

export interface ConfirmResponse {
  ticket_id: string;
  status: string;
  customer_name?: string;
  delivery?: {
    prorouting_order_id?: string;
    logistics_partner?: string;
    delivery_price?: number;
    order_state?: string;
    pickup_address?: string;
    drop_address?: string;
  };
  message?: string;
  error?: string;
}

export interface DeliveryResponse {
  ticket_id: string;
  ticket_status: string;
  delivery: {
    order_state?: string;
    prorouting_order_id?: string;
    logistics_partner?: string;
    delivery_price?: number;
    pickup?: { store_name?: string; address?: string; phone?: string };
    drop?: { customer_name?: string; address?: string; phone?: string };
    rider?: { name?: string; phone?: string } | null;
    tracking_url?: string;
    error?: string;
  };
  error?: string;
}

// ── API Functions ────────────────────────────────────────────────────────

export function createTicket(data: TicketRequest): Promise<TicketResponse> {
  return request("/api/ticket", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getTicketStatus(ticketId: string): Promise<TicketStatus> {
  return request(`/api/ticket/${ticketId}`);
}

export function getTicketOptions(ticketId: string): Promise<OptionsResponse> {
  return request(`/api/ticket/${ticketId}/options`);
}

export function confirmTicket(
  ticketId: string,
  storeCallId: number,
  customerName?: string
): Promise<ConfirmResponse> {
  return request(`/api/ticket/${ticketId}/confirm`, {
    method: "POST",
    body: JSON.stringify({
      store_call_id: storeCallId,
      customer_name: customerName,
    }),
  });
}

export function getDeliveryStatus(
  ticketId: string
): Promise<DeliveryResponse> {
  return request(`/api/ticket/${ticketId}/delivery`);
}
