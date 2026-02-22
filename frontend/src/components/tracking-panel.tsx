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
  Globe,
  Tag,
  Zap,
  ChevronDown,
  ChevronUp,
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
              <ResultCard result={ticket.result} />
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

// ── Result Card ──────────────────────────────────────────────────────────

function ResultCard({ result }: { result: Record<string, unknown> }) {
  const [showRawJson, setShowRawJson] = useState(false);

  const r = result as Record<string, unknown>;
  const status = r.status as string | undefined;
  const recommendation = r.recommendation as string | undefined;
  const productRequested = r.product_requested as string | undefined;
  const storesContacted = r.stores_contacted as number | undefined;
  const callsConnected = r.calls_connected as number | undefined;
  const callsFailed = r.calls_failed as number | undefined;
  const storesWithProduct = r.stores_with_product as number | undefined;

  const bestOption = r.best_option as Record<string, unknown> | undefined;
  const allOptions = r.all_options as Record<string, unknown>[] | undefined;

  const webDeals = r.web_deals as Record<string, unknown> | undefined;
  const deals = webDeals?.deals as Record<string, unknown>[] | undefined;
  const bestDeal = webDeals?.best_deal as Record<string, unknown> | undefined;
  const searchSummary = webDeals?.search_summary as string | undefined;
  const priceRange = webDeals?.price_range as Record<string, number> | undefined;

  const strippedResult = { ...r };
  delete strippedResult.web_deals;
  delete strippedResult.all_options;
  delete strippedResult.best_option;
  const hasRawData = Object.keys(strippedResult).length > 5;

  return (
    <div className="space-y-3">
      {/* Summary card */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Result</CardTitle>
            {status && (
              <Badge
                className={
                  status === "found"
                    ? "bg-green-500/20 text-green-400"
                    : "bg-yellow-500/20 text-yellow-400"
                }
              >
                {status}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {productRequested && (
            <p className="text-muted-foreground">
              Product: <span className="text-foreground font-medium">{productRequested}</span>
            </p>
          )}

          {recommendation && (
            <div className="bg-primary/5 border border-primary/10 rounded-lg p-3 text-xs leading-relaxed">
              {recommendation}
            </div>
          )}

          <div className="grid grid-cols-4 gap-2 text-center">
            {storesContacted != null && <Stat label="Stores" value={storesContacted} />}
            {callsConnected != null && <Stat label="Connected" value={callsConnected} />}
            {callsFailed != null && <Stat label="Failed" value={callsFailed} />}
            {storesWithProduct != null && <Stat label="Had Product" value={storesWithProduct} />}
          </div>
        </CardContent>
      </Card>

      {/* Best store option */}
      {bestOption && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Store className="h-4 w-4 text-green-400" />
              <CardTitle className="text-sm">Best Store Option</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium">{String(bestOption.store_name || "Unknown")}</span>
              {bestOption.price != null && (
                <span className="text-base font-bold text-green-400">
                  ₹{Number(bestOption.price).toLocaleString("en-IN")}
                </span>
              )}
            </div>
            {bestOption.matched_product && (
              <p className="text-xs text-muted-foreground">{String(bestOption.matched_product)}</p>
            )}
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {bestOption.product_match_type && (
                <Badge variant="outline" className="text-[10px]">
                  {String(bestOption.product_match_type)}
                </Badge>
              )}
              {bestOption.rating != null && <span>★ {String(bestOption.rating)}</span>}
              {bestOption.phone_number && (
                <span className="flex items-center gap-1">
                  <Phone className="h-3 w-3" />
                  {String(bestOption.phone_number)}
                </span>
              )}
              {bestOption.delivery_available != null && (
                <span>
                  Delivery: {bestOption.delivery_available ? "Yes" : "No"}
                </span>
              )}
            </div>
            {bestOption.call_summary && (
              <p className="text-xs text-muted-foreground italic">
                &quot;{String(bestOption.call_summary)}&quot;
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Other options */}
      {allOptions && allOptions.length > 1 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">All Store Options ({allOptions.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {allOptions.map((opt, i) => (
              <div key={i} className="rounded-lg border p-2.5 text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">
                    {opt.rank ? `#${opt.rank} ` : ""}{String(opt.store_name || "Store")}
                  </span>
                  {opt.price != null && (
                    <span className="font-bold text-green-400">
                      ₹{Number(opt.price).toLocaleString("en-IN")}
                    </span>
                  )}
                </div>
                {opt.matched_product && (
                  <p className="text-muted-foreground">{String(opt.matched_product)}</p>
                )}
                {opt.call_summary && (
                  <p className="text-muted-foreground italic">&quot;{String(opt.call_summary)}&quot;</p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Web deals */}
      {deals && deals.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Globe className="h-4 w-4 text-blue-400" />
              <CardTitle className="text-sm">Web Deals ({deals.length})</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {searchSummary && (
              <p className="text-xs text-muted-foreground leading-relaxed">{searchSummary}</p>
            )}

            {priceRange && (
              <div className="flex gap-4 text-xs text-muted-foreground">
                {priceRange.lowest != null && (
                  <span>Low: <span className="text-green-400 font-medium">₹{priceRange.lowest.toLocaleString("en-IN")}</span></span>
                )}
                {priceRange.avg != null && (
                  <span>Avg: ₹{Math.round(priceRange.avg).toLocaleString("en-IN")}</span>
                )}
                {priceRange.highest != null && (
                  <span>High: ₹{priceRange.highest.toLocaleString("en-IN")}</span>
                )}
              </div>
            )}

            {bestDeal && (
              <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-2.5 text-xs">
                <div className="flex items-center gap-1.5 font-medium text-blue-400 mb-1">
                  <Zap className="h-3 w-3" />
                  Best Deal: {String(bestDeal.platform)}
                  {bestDeal.price != null && (
                    <span className="ml-auto font-bold text-green-400">
                      ₹{Number(bestDeal.price).toLocaleString("en-IN")}
                    </span>
                  )}
                </div>
                {bestDeal.reason && (
                  <p className="text-muted-foreground leading-relaxed">{String(bestDeal.reason)}</p>
                )}
              </div>
            )}

            <div className="space-y-2">
              {deals.map((deal, i) => {
                const d = deal as Record<string, unknown>;
                return (
                  <div
                    key={i}
                    className="rounded-lg border border-blue-500/20 p-2.5 text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge
                        variant="outline"
                        className="text-[10px] border-blue-400/30 text-blue-400"
                      >
                        {String(d.platform)}
                      </Badge>
                      <div className="flex items-center gap-2">
                        {d.original_price != null && d.original_price !== d.price && (
                          <span className="text-muted-foreground line-through">
                            ₹{Number(d.original_price).toLocaleString("en-IN")}
                          </span>
                        )}
                        {d.price != null && (
                          <span className="font-bold text-green-400">
                            ₹{Number(d.price).toLocaleString("en-IN")}
                          </span>
                        )}
                        {d.discount_percent != null && Number(d.discount_percent) > 0 && (
                          <span className="text-[9px] font-semibold text-orange-400 bg-orange-500/15 px-1 py-px rounded">
                            -{String(d.discount_percent)}%
                          </span>
                        )}
                      </div>
                    </div>
                    {d.product_title && (
                      <p className="text-muted-foreground">{String(d.product_title)}</p>
                    )}
                    {d.delivery_estimate && (
                      <div className="flex items-center gap-1 text-muted-foreground">
                        <Zap className="h-2.5 w-2.5 text-yellow-400" />
                        <span>Delivery: {String(d.delivery_estimate)}</span>
                      </div>
                    )}
                    {d.confidence && (
                      <div>
                        <span
                          className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                            d.confidence === "high"
                              ? "bg-green-500/15 text-green-400"
                              : d.confidence === "medium"
                                ? "bg-yellow-500/15 text-yellow-400"
                                : "bg-muted text-muted-foreground/70"
                          }`}
                        >
                          {d.confidence === "high"
                            ? "High confidence match"
                            : d.confidence === "medium"
                              ? "Medium confidence"
                              : "Low confidence — verify before buying"}
                        </span>
                      </div>
                    )}
                    {d.offer_details && (
                      <div className="flex items-start gap-1 text-orange-400/80">
                        <Tag className="h-2.5 w-2.5 shrink-0 mt-0.5" />
                        <span>{String(d.offer_details)}</span>
                      </div>
                    )}
                    {d.url && (
                      <a
                        href={String(d.url)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-blue-400 hover:underline"
                      >
                        <ExternalLink className="h-3 w-3" />
                        View on {String(d.platform)}
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Raw JSON toggle (for debugging) */}
      {hasRawData && (
        <button
          onClick={() => setShowRawJson(!showRawJson)}
          className="flex items-center gap-1 text-[10px] text-muted-foreground/50 hover:text-muted-foreground transition-colors mx-auto"
        >
          {showRawJson ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {showRawJson ? "Hide" : "Show"} raw data
        </button>
      )}
      {showRawJson && (
        <Card>
          <CardContent className="p-3">
            <pre className="text-[10px] bg-muted p-3 rounded-lg overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
              {JSON.stringify(strippedResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
