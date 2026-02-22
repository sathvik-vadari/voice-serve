"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { QueryPanel } from "@/components/query-panel";
import { TrackingPanel } from "@/components/tracking-panel";
import { Package, Search } from "lucide-react";

export default function Home() {
  return (
    <main className="min-h-dvh bg-background">
      <div className="mx-auto max-w-3xl px-3 sm:px-4 py-3 sm:py-4">
        <header className="mb-3 text-center">
          <h1 className="text-lg sm:text-xl font-bold tracking-tight">Voice Commerce</h1>
        </header>

        <Tabs defaultValue="query" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="query" className="gap-1.5 text-xs sm:text-sm">
              <Search className="h-4 w-4" />
              New Query
            </TabsTrigger>
            <TabsTrigger value="tracking" className="gap-1.5 text-xs sm:text-sm">
              <Package className="h-4 w-4" />
              Track Order
            </TabsTrigger>
          </TabsList>

          <TabsContent value="query" className="mt-3 sm:mt-4" forceMount>
            <QueryPanel />
          </TabsContent>

          <TabsContent value="tracking" className="mt-3 sm:mt-4" forceMount>
            <TrackingPanel />
          </TabsContent>
        </Tabs>
      </div>
    </main>
  );
}
