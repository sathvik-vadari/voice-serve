"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  createTicket,
  getTicketStatus,
  getTicketOptions,
  confirmTicket,
  type TicketStatus,
  type OptionsResponse,
  type ConfirmResponse,
  type OptionItem,
  type WebDeal,
} from "@/lib/api";
import {
  Loader2,
  Send,
  CheckCircle2,
  XCircle,
  Phone,
  MapPin,
  Store,
  RefreshCw,
  Truck,
  Circle,
  Check,
  Minus,
  Plus,
  Globe,
  ExternalLink,
  Tag,
  Zap,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

type ChatMessage = {
  id: string;
  role: "user" | "bot";
  content: string;
  timestamp: Date;
  options?: OptionItem[];
  webDeals?: WebDeal[];
  showConfirm?: boolean;
  deliveryInfo?: ConfirmResponse["delivery"];
};

type FlowPhase =
  | "input"
  | "polling"
  | "options_ready"
  | "awaiting_confirm"
  | "confirming"
  | "done"
  | "rejected"
  | "retry_input";

// Ordered pipeline steps — each maps to one or more backend statuses
const PIPELINE_STEPS = [
  {
    key: "classifying",
    label: "Classifying query",
    statuses: ["received", "classifying"],
  },
  { key: "analyzing", label: "Analyzing request", statuses: ["analyzing"] },
  {
    key: "researching",
    label: "Researching product",
    statuses: ["researching"],
  },
  {
    key: "finding_stores",
    label: "Finding nearby stores",
    statuses: ["finding_stores"],
  },
  {
    key: "calling_stores",
    label: "Calling stores",
    statuses: ["calling_stores"],
  },
  { key: "completed", label: "Done", statuses: ["completed"] },
] as const;

function mkId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function QueryPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [phase, setPhase] = useState<FlowPhase>("input");

  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [query, setQuery] = useState("");
  const [maxStores, setMaxStores] = useState(4);

  const [ticketId, setTicketId] = useState<string | null>(null);
  const [options, setOptions] = useState<OptionsResponse | null>(null);
  const [selectedOption, setSelectedOption] = useState<OptionItem | null>(null);

  // Pipeline tracker state (updated in-place, not as chat messages)
  const [pipelineStatus, setPipelineStatus] = useState<string>("");
  const [callProgress, setCallProgress] = useState<{
    completed: number;
    total: number;
  } | null>(null);
  const [hasWebDeals, setHasWebDeals] = useState(false);

  const lastStatusRef = useRef("");
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const addMsg = useCallback(
    (
      role: ChatMessage["role"],
      content: string,
      extra?: Partial<ChatMessage>,
    ) => {
      setMessages((prev) => [
        ...prev,
        { id: mkId(), role, content, timestamp: new Date(), ...extra },
      ]);
    },
    [],
  );

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pipelineStatus]);

  // ── Submit query ───────────────────────────────────────────────────────

  const handleSubmit = async () => {
    if (!query.trim() || !address.trim() || !phone.trim()) return;

    const details = [
      query,
      "",
      `📍  ${address}`,
      `📞  ${phone}${name ? `  ·  ${name}` : ""}`,
    ].join("\n");
    addMsg("user", details);
    setPhase("polling");
    setPipelineStatus("");
    setCallProgress(null);
    setHasWebDeals(false);
    lastStatusRef.current = "";

    try {
      const res = await createTicket({
        query: query.trim(),
        location: address.trim(),
        user_phone: phone.trim(),
        user_name: name.trim() || undefined,
        max_stores: maxStores,
      });

      if (res.status === "rejected") {
        addMsg("bot", res.message);
        setPhase("input");
        return;
      }

      setTicketId(res.ticket_id);
      addMsg(
        "bot",
        `Got it! Your ticket is **${res.ticket_id}**. Working on it now...`,
      );
      startPolling(res.ticket_id);
    } catch {
      addMsg("bot", "Failed to connect to the server. Is the backend running?");
      setPhase("input");
    }
  };

  // ── Polling ────────────────────────────────────────────────────────────

  const startPolling = useCallback(
    (tid: string) => {
      if (pollingRef.current) clearInterval(pollingRef.current);

      pollingRef.current = setInterval(async () => {
        try {
          const status: TicketStatus = await getTicketStatus(tid);

          if (status.status !== lastStatusRef.current) {
            lastStatusRef.current = status.status;
            setPipelineStatus(status.status);
          }

          if (status.progress) {
            setCallProgress({
              completed: status.progress.calls_completed,
              total: status.progress.calls_total,
            });
          }

          if (status.web_deals && status.web_deals.deals?.length > 0) {
            setHasWebDeals(true);
          }

          if (status.status === "failed") {
            clearInterval(pollingRef.current!);
            pollingRef.current = null;
            setPipelineStatus("");
            addMsg(
              "bot",
              `Something went wrong: ${status.error || "Unknown error"}. You can try again.`,
            );
            setPhase("rejected");
            return;
          }

          if (status.status === "completed") {
            clearInterval(pollingRef.current!);
            pollingRef.current = null;
            setPipelineStatus("");
            await fetchOptions(tid);
          }
        } catch {
          // network blip — keep polling
        }
      }, 3000);
    },
    [addMsg],
  );

  // ── Fetch options ──────────────────────────────────────────────────────

  const fetchOptions = async (tid: string) => {
    try {
      const opts = await getTicketOptions(tid);
      setOptions(opts);

      if (opts.error) {
        addMsg("bot", opts.error);
        setPhase("input");
        return;
      }

      if (
        opts.options_found === 0 &&
        (!opts.web_deals || opts.web_deals.length === 0)
      ) {
        addMsg("bot", opts.message);
        setPhase("rejected");
        return;
      }

      if (
        opts.options_found === 0 &&
        opts.web_deals &&
        opts.web_deals.length > 0
      ) {
        addMsg("bot", opts.message, { webDeals: opts.web_deals });
        setPhase("rejected");
        return;
      }

      addMsg("bot", opts.message, {
        options: opts.options,
        webDeals: opts.web_deals,
        showConfirm: true,
      });
      setPhase("awaiting_confirm");
    } catch {
      addMsg("bot", "Couldn't fetch options. Try polling again in a moment.");
      setPhase("input");
    }
  };

  // ── Confirm / Reject ──────────────────────────────────────────────────

  const handleConfirm = async (option: OptionItem) => {
    setSelectedOption(option);
    setPhase("confirming");
    addMsg(
      "user",
      `Yes, go with **${option.store_name}** — ₹${option.price ?? "N/A"}`,
    );

    try {
      const res = await confirmTicket(
        ticketId!,
        option.store_call_id,
        name || undefined,
      );

      if (res.error) {
        addMsg("bot", `Order failed: ${res.error}`);
        setPhase("rejected");
        return;
      }

      addMsg("bot", res.message || "Order confirmed!", {
        deliveryInfo: res.delivery,
      });
      setPhase("done");
    } catch {
      addMsg("bot", "Failed to confirm order. Please try again.");
      setPhase("awaiting_confirm");
    }
  };

  const handleReject = () => {
    setPhase("rejected");
    addMsg(
      "bot",
      "No worries! Looks like that didn't work out. Would you like to:\n\n**Try Again** — fix your details (name, phone, address)\n\n**New Query** — describe what you need differently (be more specific!)",
    );
  };

  const handleTryAgain = () => {
    setPhase("retry_input");
    addMsg("bot", "Update your details below and re-submit.");
  };

  const handleNewQuery = () => {
    setQuery("");
    setPhase("input");
    addMsg(
      "bot",
      "Enter a new query below — try to be more descriptive this time!",
    );
  };

  const handleRetrySubmit = async () => {
    if (!address.trim() || !phone.trim()) return;
    const details = [
      `Retrying: ${query}`,
      "",
      `📍  ${address}`,
      `📞  ${phone}${name ? `  ·  ${name}` : ""}`,
    ].join("\n");
    addMsg("user", details);
    setPhase("polling");
    setPipelineStatus("");
    setCallProgress(null);
    setHasWebDeals(false);
    lastStatusRef.current = "";

    try {
      const res = await createTicket({
        query: query.trim(),
        location: address.trim(),
        user_phone: phone.trim(),
        user_name: name.trim() || undefined,
        max_stores: maxStores,
      });

      if (res.status === "rejected") {
        addMsg("bot", res.message);
        setPhase("retry_input");
        return;
      }

      setTicketId(res.ticket_id);
      addMsg("bot", `New ticket **${res.ticket_id}** created. Processing...`);
      startPolling(res.ticket_id);
    } catch {
      addMsg("bot", "Failed to connect. Is the backend running?");
      setPhase("retry_input");
    }
  };

  const handleReset = () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    setMessages([]);
    setPhase("input");
    setTicketId(null);
    setPipelineStatus("");
    setCallProgress(null);
    setHasWebDeals(false);
    lastStatusRef.current = "";
    setOptions(null);
    setSelectedOption(null);
    setQuery("");
  };

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col" style={{ height: "calc(100vh - 200px)" }}>
      <ScrollArea className="flex-1 pr-2">
        <div className="space-y-3 pb-4">
          {messages.length === 0 && phase === "input" && (
            <div className="py-12 text-center text-muted-foreground text-sm">
              Fill in your details below and tell us what you need!
            </div>
          )}

          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              onConfirm={handleConfirm}
              onReject={handleReject}
              phase={phase}
            />
          ))}

          {/* Live pipeline tracker — replaces spammy status messages */}
          {phase === "polling" && pipelineStatus && (
            <PipelineTracker
              currentStatus={pipelineStatus}
              callProgress={callProgress}
              hasWebDeals={hasWebDeals}
            />
          )}

          <div ref={scrollRef} />
        </div>
      </ScrollArea>

      <Separator className="my-3" />

      {/* Input area */}
      {(phase === "input" || phase === "retry_input") && (
        <div className="space-y-3 pb-6">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label
                htmlFor="name"
                className="text-xs text-muted-foreground mb-1"
              >
                Name
              </Label>
              <Input
                id="name"
                placeholder="Your name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <Label
                htmlFor="phone"
                className="text-xs text-muted-foreground mb-1"
              >
                Phone *
              </Label>
              <Input
                id="phone"
                placeholder="+91..."
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
          </div>

          <div>
            <Label
              htmlFor="address"
              className="text-xs text-muted-foreground mb-1"
            >
              Delivery Address *
            </Label>
            <Input
              id="address"
              placeholder="Full address with area & city"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>

          <div className="flex flex-col items-center gap-1">
            <Label className="text-xs text-muted-foreground">
              Stores to call
            </Label>
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => setMaxStores((v) => Math.max(1, v - 1))}
                disabled={maxStores <= 1}
              >
                <Minus className="h-3.5 w-3.5" />
              </Button>
              <span className="w-8 text-center text-sm font-medium tabular-nums">
                {maxStores}
              </span>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => setMaxStores((v) => Math.min(10, v + 1))}
                disabled={maxStores >= 10}
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </div>
            <span className="text-[11px] text-muted-foreground">
              More stores = wider reach but takes longer
            </span>
          </div>

          {phase === "input" && (
            <div>
              <Label
                htmlFor="query"
                className="text-xs text-muted-foreground mb-1"
              >
                What do you need? *
              </Label>
              <Textarea
                id="query"
                placeholder="e.g. iPhone 15 128GB black, or 2 butter naans from a nearby restaurant"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                rows={2}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
              />
            </div>
          )}

          <Button
            className="w-full"
            onClick={phase === "retry_input" ? handleRetrySubmit : handleSubmit}
            disabled={
              !phone.trim() ||
              !address.trim() ||
              (phase === "input" && !query.trim())
            }
          >
            <Send className="mr-2 h-4 w-4 pb-6" />
            {phase === "retry_input"
              ? "Retry with Updated Details"
              : "Send Query"}
          </Button>
        </div>
      )}

      {phase === "polling" && !pipelineStatus && (
        <div className="flex items-center justify-center gap-2 py-4 pb-8 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">Connecting...</span>
        </div>
      )}

      {phase === "confirming" && (
        <div className="flex items-center justify-center gap-2 py-4 pb-8 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">Placing your delivery order...</span>
        </div>
      )}

      {phase === "rejected" && (
        <div className="flex gap-3 pb-6">
          <Button variant="outline" className="flex-1" onClick={handleTryAgain}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Try Again
          </Button>
          <Button className="flex-1" onClick={handleNewQuery}>
            <Send className="mr-2 h-4 w-4" />
            New Query
          </Button>
        </div>
      )}

      {phase === "done" && (
        <div className="text-center pb-6">
          <Button variant="outline" onClick={handleReset}>
            Start New Query
          </Button>
          {ticketId && (
            <p className="mt-2 text-xs text-muted-foreground">
              Track your order in the &quot;Track Order&quot; tab with ticket{" "}
              <span className="font-mono font-bold">{ticketId}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Pipeline Tracker ─────────────────────────────────────────────────────

function PipelineTracker({
  currentStatus,
  callProgress,
  hasWebDeals,
}: {
  currentStatus: string;
  callProgress: { completed: number; total: number } | null;
  hasWebDeals: boolean;
}) {
  const currentIdx = PIPELINE_STEPS.findIndex((s) =>
    (s.statuses as readonly string[]).includes(currentStatus),
  );

  const pastResearch = currentIdx > 2;
  const webSearchActive = pastResearch && !hasWebDeals;
  const webSearchDone = hasWebDeals;

  return (
    <div className="mx-auto max-w-[85%]">
      <Card className="bg-muted/50 border-muted">
        <CardContent className="p-4">
          <div className="space-y-1">
            {PIPELINE_STEPS.map((step, idx) => {
              const isDone = idx < currentIdx;
              const isCurrent = idx === currentIdx;

              return (
                <div key={step.key} className="flex items-center gap-3">
                  {isDone ? (
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-500/20">
                      <Check className="h-3 w-3 text-green-400" />
                    </div>
                  ) : isCurrent ? (
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center">
                      <Loader2 className="h-4 w-4 animate-spin text-primary" />
                    </div>
                  ) : (
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center">
                      <Circle className="h-3 w-3 text-muted-foreground/40" />
                    </div>
                  )}

                  <span
                    className={`text-sm ${
                      isDone
                        ? "text-muted-foreground line-through"
                        : isCurrent
                          ? "text-foreground font-medium"
                          : "text-muted-foreground/40"
                    }`}
                  >
                    {step.label}
                  </span>

                  {isCurrent &&
                    step.key === "calling_stores" &&
                    callProgress && (
                      <Badge
                        variant="outline"
                        className="ml-auto text-[10px] font-mono"
                      >
                        {callProgress.completed}/{callProgress.total}
                      </Badge>
                    )}
                </div>
              );
            })}

            {/* Parallel web search indicator */}
            {(webSearchActive || webSearchDone) && (
              <div className="flex items-center gap-3">
                {webSearchDone ? (
                  <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-500/20">
                    <Check className="h-3 w-3 text-green-400" />
                  </div>
                ) : (
                  <div className="flex h-5 w-5 shrink-0 items-center justify-center">
                    <Globe className="h-4 w-4 animate-pulse text-blue-400" />
                  </div>
                )}
                <span
                  className={`text-sm ${
                    webSearchDone
                      ? "text-muted-foreground line-through"
                      : "text-blue-400 font-medium"
                  }`}
                >
                  Searching web for deals
                </span>
                {webSearchActive && (
                  <Badge
                    variant="outline"
                    className="ml-auto text-[10px] text-blue-400 border-blue-400/30"
                  >
                    parallel
                  </Badge>
                )}
              </div>
            )}
          </div>

          {currentStatus === "calling_stores" &&
            callProgress &&
            callProgress.total > 0 && (
              <div className="mt-3 max-w-full min-w-0">
                <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${Math.max(
                        5,
                        (callProgress.completed / callProgress.total) * 100,
                      )}%`,
                    }}
                  />
                </div>
                <p className="mt-1 text-[10px] text-muted-foreground text-right">
                  {callProgress.completed} of {callProgress.total} calls done
                </p>
              </div>
            )}
        </CardContent>
      </Card>
    </div>
  );
}

// ── Message Bubble ───────────────────────────────────────────────────────

function MessageBubble({
  msg,
  onConfirm,
  onReject,
  phase,
}: {
  msg: ChatMessage;
  onConfirm: (opt: OptionItem) => void;
  onReject: () => void;
  phase: FlowPhase;
}) {
  const isUser = msg.role === "user";
  const bubbleWidthClass = isUser
    ? "max-w-[75%] w-fit"
    : "max-w-[min(100%,36rem)] w-full";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`${bubbleWidthClass} min-w-0 rounded-2xl px-4 py-2.5 text-sm overflow-hidden ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground"
        }`}
      >
        <div className="whitespace-pre-wrap">
          {renderMarkdownLite(msg.content)}
        </div>

        {msg.options && msg.options.length > 0 && (
          <div className="mt-3 space-y-2">
            {msg.options.map((opt, i) => (
              <OptionCard
                key={opt.store_call_id}
                option={opt}
                index={i + 1}
                canConfirm={
                  phase === "awaiting_confirm" && msg.showConfirm === true
                }
                onConfirm={onConfirm}
              />
            ))}

            {phase === "awaiting_confirm" && msg.showConfirm && (
              <Button
                variant="ghost"
                size="sm"
                className="w-full mt-1 text-destructive hover:text-destructive"
                onClick={onReject}
              >
                <XCircle className="mr-2 h-4 w-4" />
                None of these work
              </Button>
            )}
          </div>
        )}

        {msg.webDeals && msg.webDeals.length > 0 && (
          <WebDealsCarousel deals={msg.webDeals} />
        )}

        {msg.deliveryInfo && (
          <Card className="mt-3 bg-background/50">
            <CardContent className="p-3 text-xs space-y-1">
              <div className="flex items-center gap-2 font-medium text-sm">
                <Truck className="h-4 w-4" /> Delivery Details
              </div>
              {msg.deliveryInfo.logistics_partner && (
                <p>Partner: {msg.deliveryInfo.logistics_partner}</p>
              )}
              {msg.deliveryInfo.delivery_price != null && (
                <p>Cost: ₹{msg.deliveryInfo.delivery_price}</p>
              )}
              {msg.deliveryInfo.order_state && (
                <p>Status: {msg.deliveryInfo.order_state}</p>
              )}
              {msg.deliveryInfo.pickup_address && (
                <p className="text-muted-foreground">
                  From: {msg.deliveryInfo.pickup_address}
                </p>
              )}
            </CardContent>
          </Card>
        )}

        <p className="mt-1 text-[10px] opacity-40">
          {msg.timestamp.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      </div>
    </div>
  );
}

// ── Option Card ──────────────────────────────────────────────────────────

function OptionCard({
  option,
  index,
  canConfirm,
  onConfirm,
}: {
  option: OptionItem;
  index: number;
  canConfirm: boolean;
  onConfirm: (opt: OptionItem) => void;
}) {
  return (
    <Card className="bg-background/50">
      <CardContent className="p-3">
        <div className="flex items-start justify-between">
          <div className="space-y-1 flex-1">
            <div className="flex items-center gap-2">
              <Store className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-medium text-sm">
                {index}. {option.store_name}
              </span>
              {option.product_match_type && (
                <Badge
                  variant={
                    option.product_match_type === "exact"
                      ? "default"
                      : "secondary"
                  }
                  className="text-[10px]"
                >
                  {option.product_match_type}
                </Badge>
              )}
            </div>

            {option.matched_product && (
              <p className="text-xs text-muted-foreground">
                {option.matched_product}
              </p>
            )}

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {option.price != null && <span>₹{option.price}</span>}
              {option.rating != null && <span>★ {option.rating}</span>}
              {option.address && (
                <span className="flex items-center gap-1">
                  <MapPin className="h-3 w-3" />
                  {option.address.length > 50
                    ? option.address.slice(0, 50) + "…"
                    : option.address}
                </span>
              )}
              {option.phone_number && (
                <span className="flex items-center gap-1">
                  <Phone className="h-3 w-3" />
                  {option.phone_number}
                </span>
              )}
            </div>

            {option.call_summary && (
              <p className="text-xs italic text-muted-foreground mt-1">
                &quot;{option.call_summary}&quot;
              </p>
            )}
          </div>

          {canConfirm && (
            <Button
              size="sm"
              className="ml-2 shrink-0"
              onClick={() => onConfirm(option)}
            >
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              Go
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Web Deals Carousel ───────────────────────────────────────────────────

function WebDealsCarousel({ deals }: { deals: WebDeal[] }) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [current, setCurrent] = useState(0);
  const total = deals.length;

  const scrollTo = useCallback((idx: number) => {
    const track = trackRef.current;
    if (!track) return;
    const child = track.children[idx] as HTMLElement | undefined;
    if (child) {
      track.scrollTo({ left: child.offsetLeft - 4, behavior: "smooth" });
    }
  }, []);

  const goTo = useCallback(
    (idx: number) => {
      const clamped = Math.max(0, Math.min(idx, total - 1));
      setCurrent(clamped);
      scrollTo(clamped);
    },
    [total, scrollTo],
  );

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const scrollLeft = track.scrollLeft;
        const children = Array.from(track.children) as HTMLElement[];
        let closest = 0;
        let minDist = Infinity;
        children.forEach((child, i) => {
          const dist = Math.abs(child.offsetLeft - scrollLeft);
          if (dist < minDist) {
            minDist = dist;
            closest = i;
          }
        });
        setCurrent(closest);
        ticking = false;
      });
    };
    track.addEventListener("scroll", onScroll, { passive: true });
    return () => track.removeEventListener("scroll", onScroll);
  }, [deals]);

  return (
    <div className="mt-3 max-w-full min-w-0">
      {/* Header */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Globe className="h-3.5 w-3.5 text-blue-400" />
          <span className="font-medium text-xs">Online Deals</span>
          <Badge
            variant="outline"
            className="text-[9px] px-1.5 py-0 h-4 text-blue-400 border-blue-400/30"
          >
            {total}
          </Badge>
        </div>

        {total > 1 && (
          <div className="flex items-center gap-0.5">
            <button
              onClick={() => goTo(current - 1)}
              disabled={current === 0}
              className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-25 transition-opacity"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <span className="text-[10px] tabular-nums text-muted-foreground min-w-[28px] text-center">
              {current + 1}/{total}
            </span>
            <button
              onClick={() => goTo(current + 1)}
              disabled={current === total - 1}
              className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground disabled:opacity-25 transition-opacity"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* Scrollable track — cards peek from right */}
      <div
        ref={trackRef}
        className="flex w-full max-w-full min-w-0 gap-1.5 overflow-x-auto snap-x snap-mandatory scrollbar-none"
        style={{ scrollbarWidth: "none", msOverflowStyle: "none" }}
      >
        {deals.map((deal, i) => (
          <div
            key={`${deal.platform}-${i}`}
            className="snap-start shrink-0"
            style={{ flexBasis: total > 1 ? "78%" : "100%" }}
          >
            <WebDealSlide deal={deal} />
          </div>
        ))}
        {/* Spacer so last card can scroll fully into view */}
        {total > 1 && <div className="shrink-0 w-1" />}
      </div>

      {/* Dots */}
      {total > 1 && (
        <div className="flex justify-center gap-1 mt-1.5">
          {deals.map((_, i) => (
            <button
              key={i}
              onClick={() => goTo(i)}
              className={`rounded-full transition-all duration-200 ${
                i === current
                  ? "w-3.5 h-1 bg-blue-400"
                  : "w-1 h-1 bg-muted-foreground/30 hover:bg-muted-foreground/50"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function WebDealSlide({ deal }: { deal: WebDeal }) {
  return (
    <div className="rounded-lg border border-blue-500/20 bg-background/50 px-2.5 py-2 space-y-1">
      {/* Row 1: platform + price */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold truncate">{deal.platform}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          {deal.price != null && (
            <span className="text-sm font-bold text-green-400">
              ₹{deal.price.toLocaleString("en-IN")}
            </span>
          )}
          {deal.discount_percent != null && deal.discount_percent > 0 && (
            <span className="text-[9px] font-medium text-orange-400 bg-orange-500/15 px-1 py-px rounded">
              -{deal.discount_percent}%
            </span>
          )}
          {deal.url && (
            <a
              href={deal.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300"
            >
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>

      {/* Row 2: product title (1 line) */}
      {deal.product_title && (
        <p className="text-[11px] text-muted-foreground truncate">
          {deal.product_title}
        </p>
      )}

      {/* Row 3: meta chips */}
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
        {deal.original_price != null && deal.original_price !== deal.price && (
          <span className="line-through">
            ₹{deal.original_price.toLocaleString("en-IN")}
          </span>
        )}
        {deal.delivery_estimate && (
          <span className="flex items-center gap-0.5">
            <Zap className="h-2.5 w-2.5 text-yellow-400" />
            {deal.delivery_estimate}
          </span>
        )}
        {deal.confidence && (
          <span
            className={
              deal.confidence === "high"
                ? "text-green-400"
                : "text-muted-foreground/60"
            }
          >
            {deal.confidence}
          </span>
        )}
      </div>

      {/* Row 4: offer or notable (pick one — keep it tight) */}
      {deal.offer_details ? (
        <p className="text-[10px] text-orange-400/80 truncate">
          {deal.offer_details}
        </p>
      ) : deal.why_notable ? (
        <p className="text-[10px] text-blue-300/60 italic truncate">
          {deal.why_notable}
        </p>
      ) : null}
    </div>
  );
}

// ── Minimal markdown (bold only) ─────────────────────────────────────────

function renderMarkdownLite(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={i}>{part}</span>;
  });
}
