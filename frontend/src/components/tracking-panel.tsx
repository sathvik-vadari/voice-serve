"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  getTicketStatus,
  getDeliveryStatus,
  type TicketStatus,
  type DeliveryResponse,
  type StoreCall,
} from "@/lib/api";
import {
  Loader2,
  Search,
  RefreshCw,
  Phone,
  MapPin,
  Store,
  Truck,
  Clock,
  ExternalLink,
  CheckCircle2,
  XCircle,
  AlertCircle,
} from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
  received: "bg-blue-500/20 text-blue-400",
  classifying: "bg-blue-500/20 text-blue-400",
  analyzing: "bg-blue-500/20 text-blue-400",
  researching: "bg-yellow-500/20 text-yellow-400",
  finding_stores: "bg-yellow-500/20 text-yellow-400",
  calling_stores: "bg-orange-500/20 text-orange-400",
  completed: "bg-green-500/20 text-green-400",
  failed: "bg-red-500/20 text-red-400",
  placing_order: "bg-purple-500/20 text-purple-400",
  order_placed: "bg-purple-500/20 text-purple-400",
  agent_assigned: "bg-indigo-500/20 text-indigo-400",
  out_for_delivery: "bg-teal-500/20 text-teal-400",
  delivered: "bg-green-500/20 text-green-400",
  delivery_failed: "bg-red-500/20 text-red-400",
};

export function TrackingPanel() {
  const [ticketId, setTicketId] = useState("");
  const [ticket, setTicket] = useState<TicketStatus | null>(null);
  const [delivery, setDelivery] = useState<DeliveryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async (tid: string) => {
    try {
      setError(null);
      const status = await getTicketStatus(tid);
      if ("error" in status && status.error) {
        setError(status.error as string);
        setTicket(null);
        setDelivery(null);
        return;
      }
      setTicket(status);

      if (status.delivery) {
        try {
          const del = await getDeliveryStatus(tid);
          setDelivery(del);
        } catch {
          // no delivery yet
        }
      }
    } catch {
      setError("Failed to connect to server");
    }
  }, []);

  const handleSearch = async () => {
    if (!ticketId.trim()) return;
    setLoading(true);
    await fetchData(ticketId.trim());
    setLoading(false);
  };

  useEffect(() => {
    if (autoRefresh && ticketId) {
      intervalRef.current = setInterval(() => fetchData(ticketId), 5000);
      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }
    if (intervalRef.current) clearInterval(intervalRef.current);
  }, [autoRefresh, ticketId, fetchData]);

  const terminalStatuses = ["completed", "failed", "delivered", "delivery_failed"];
  const isTerminal = ticket && terminalStatuses.includes(ticket.status);

  return (
    <div className="space-y-4" style={{ minHeight: "calc(100vh - 200px)" }}>
      {/* Search bar */}
      <div className="flex gap-2">
        <div className="flex-1">
          <Label htmlFor="track-id" className="text-xs text-muted-foreground mb-1">
            Ticket ID
          </Label>
          <Input
            id="track-id"
            placeholder="TKT-001"
            value={ticketId}
            onChange={(e) => setTicketId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSearch();
            }}
          />
        </div>
        <div className="flex items-end gap-2">
          <Button onClick={handleSearch} disabled={loading || !ticketId.trim()}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
          </Button>
          <Button
            variant={autoRefresh ? "default" : "outline"}
            onClick={() => setAutoRefresh(!autoRefresh)}
            disabled={!ticketId.trim()}
            title="Auto-refresh every 5s"
          >
            <RefreshCw className={`h-4 w-4 ${autoRefresh ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive/50">
          <CardContent className="p-4 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle className="h-4 w-4" />
            {error}
          </CardContent>
        </Card>
      )}

      {ticket && (
        <ScrollArea className="h-[calc(100vh-320px)]">
          <div className="space-y-4 pr-2">
            {/* Status overview */}
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">
                    {ticket.ticket_id}
                  </CardTitle>
                  <Badge className={STATUS_COLORS[ticket.status] || "bg-muted"}>
                    {ticket.status.replace(/_/g, " ")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                {ticket.query_type && (
                  <p className="text-muted-foreground">
                    Type: <span className="text-foreground">{ticket.query_type}</span>
                  </p>
                )}
                {ticket.created_at && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Created: {new Date(ticket.created_at).toLocaleString()}
                  </p>
                )}
                {ticket.updated_at && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Updated: {new Date(ticket.updated_at).toLocaleString()}
                  </p>
                )}
                {ticket.error && (
                  <p className="text-sm text-destructive mt-2">{ticket.error}</p>
                )}
              </CardContent>
            </Card>

            {/* Progress */}
            {ticket.progress && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Call Progress</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-4 gap-2 text-center">
                    <Stat label="Stores" value={ticket.progress.stores_found} />
                    <Stat label="Total Calls" value={ticket.progress.calls_total} />
                    <Stat label="Completed" value={ticket.progress.calls_completed} />
                    <Stat label="In Progress" value={ticket.progress.calls_in_progress} />
                  </div>
                  <div className="mt-2 h-2 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all duration-500"
                      style={{
                        width: `${
                          ticket.progress.calls_total
                            ? (ticket.progress.calls_completed / ticket.progress.calls_total) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Product info */}
            {ticket.product && <ProductCard product={ticket.product} />}

            {/* Store calls */}
            {ticket.store_calls && ticket.store_calls.length > 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Store Calls</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {ticket.store_calls.map((call) => (
                    <StoreCallCard key={call.id} call={call} />
                  ))}
                </CardContent>
              </Card>
            )}

            {/* Delivery */}
            {(ticket.delivery || delivery) && (
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <Truck className="h-4 w-4" />
                    <CardTitle className="text-sm">Delivery</CardTitle>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {(delivery?.delivery || ticket.delivery) && (
                    <DeliveryDetails
                      delivery={(delivery?.delivery || ticket.delivery!) as Record<string, unknown>}
                    />
                  )}
                </CardContent>
              </Card>
            )}

            {/* Final result */}
            {ticket.result && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Result</CardTitle>
                </CardHeader>
                <CardContent className="text-sm">
                  <pre className="text-xs bg-muted p-3 rounded-lg overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(ticket.result, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            )}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}

function ProductCard({ product }: { product: Record<string, unknown> }) {
  const name = String(product.product_name || "Unknown");
  const category = product.product_category ? String(product.product_category) : null;
  const avgPrice = product.avg_price_online != null ? String(product.avg_price_online) : null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Product</CardTitle>
      </CardHeader>
      <CardContent className="text-sm space-y-1">
        <p className="font-medium">{name}</p>
        {category && (
          <p className="text-muted-foreground text-xs">Category: {category}</p>
        )}
        {avgPrice && (
          <p className="text-muted-foreground text-xs">Avg online price: ₹{avgPrice}</p>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-lg font-bold">{value}</p>
      <p className="text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}

function StoreCallCard({ call }: { call: StoreCall }) {
  const [expanded, setExpanded] = useState(false);

  const statusIcon =
    call.status === "analyzed" && call.product_available ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />
    ) : call.status === "failed" ? (
      <XCircle className="h-3.5 w-3.5 text-red-400" />
    ) : (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-yellow-400" />
    );

  return (
    <div className="rounded-lg border p-3 space-y-1">
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {statusIcon}
          <Store className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-sm font-medium">{call.store_name}</span>
        </div>
        <Badge variant="outline" className="text-[10px]">
          {call.status}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {call.price != null && <span>₹{call.price}</span>}
        {call.product_match_type && <span>Match: {call.product_match_type}</span>}
        {call.rating != null && <span>★ {call.rating}</span>}
        {call.phone_number && (
          <span className="flex items-center gap-1">
            <Phone className="h-3 w-3" />
            {call.phone_number}
          </span>
        )}
      </div>

      {call.matched_product && (
        <p className="text-xs text-muted-foreground">
          Found: {call.matched_product}
        </p>
      )}

      {call.notes && (
        <p className="text-xs italic text-muted-foreground">{call.notes}</p>
      )}

      {expanded && call.transcript && (
        <>
          <Separator className="my-2" />
          <div className="text-xs">
            <p className="text-muted-foreground font-medium mb-1">Transcript:</p>
            <pre className="bg-muted p-2 rounded text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">
              {call.transcript}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}

function DeliveryDetails({
  delivery,
}: {
  delivery: Record<string, unknown>;
}) {
  const d = delivery as Record<string, unknown>;
  const pickup = d.pickup as Record<string, unknown> | undefined;
  const drop = d.drop as Record<string, unknown> | undefined;
  const rider = d.rider as Record<string, unknown> | undefined;

  return (
    <div className="space-y-2 text-xs">
      {d.order_state ? (
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">State:</span>
          <Badge variant="outline">{String(d.order_state)}</Badge>
        </div>
      ) : null}
      {d.logistics_partner ? (
        <p>
          <span className="text-muted-foreground">Partner:</span>{" "}
          {String(d.logistics_partner)}
        </p>
      ) : null}
      {d.delivery_price != null ? (
        <p>
          <span className="text-muted-foreground">Cost:</span> ₹{String(d.delivery_price)}
        </p>
      ) : null}
      {(d.rider_name || rider?.name) ? (
        <p>
          <span className="text-muted-foreground">Rider:</span>{" "}
          {String(d.rider_name || rider?.name)}
          {(d.rider_phone || rider?.phone) ? ` (${String(d.rider_phone || rider?.phone)})` : null}
        </p>
      ) : null}
      {pickup?.address ? (
        <p className="flex items-center gap-1">
          <MapPin className="h-3 w-3 text-muted-foreground" />
          <span className="text-muted-foreground">Pickup:</span>{" "}
          {String(pickup.address)}
        </p>
      ) : null}
      {drop?.address ? (
        <p className="flex items-center gap-1">
          <MapPin className="h-3 w-3 text-muted-foreground" />
          <span className="text-muted-foreground">Drop:</span>{" "}
          {String(drop.address)}
        </p>
      ) : null}
      {d.tracking_url ? (
        <a
          href={String(d.tracking_url)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-primary hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
          Live Tracking
        </a>
      ) : null}
    </div>
  );
}
