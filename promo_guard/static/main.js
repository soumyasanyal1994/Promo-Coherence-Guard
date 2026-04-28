const { createApp } = Vue;

createApp({
  data() {
    return {
      config: {
        llm_providers: {
          gemini: { label: "Google Gemini", models: ["gemini-2.0-flash"] },
        },
        channels: ["web", "app", "pos"],
        taxonomy_count: 0,
        custom_endpoint_auth: [],
      },
      form: {
        llmProvider: "gemini",
        apiKey: "",
        customEndpoint: "",
        customApiAuth: "bearer",
        modelName: "gemini-2.0-flash",
        useLlmInScan: true,
        maxLlmConflicts: 35,
        channel: "web",
        asOfUtc: "",
        horizonDays: 7,
        useSamples: true,
      },
      promosFile: null,
      pricingFile: null,
      conflicts: [],
      audit: {},
      message: "",
      error: "",
      llmWarning: "",
      llmUsage: null,
      isRunning: false,
      showProgress: false,
      scanCompleted: false,
      isLightTheme: false,
      severityChart: null,
    };
  },
  methods: {
    async loadConfig() {
      const resp = await fetch("/api/config");
      const payload = await resp.json();
      this.config = payload;
      if (!this.config.custom_endpoint_auth?.length) {
        this.config.custom_endpoint_auth = [
          { value: "bearer", label: "Authorization: Bearer (default)" },
          { value: "litellm", label: "x-litellm-api-key (LiteLLM proxy)" },
        ];
      }
      const provider = this.form.llmProvider;
      const providerConfig = payload.llm_providers?.[provider];
      if (providerConfig?.models?.length) {
        this.form.modelName = providerConfig.models[0];
      }
      this.form.horizonDays = payload.default_horizon_days || 7;
      this.form.asOfUtc = this.toLocalInput(payload.default_as_of_utc);
    },
    onProviderChange() {
      const providerConfig = this.config.llm_providers?.[this.form.llmProvider];
      if (providerConfig?.models?.length) {
        this.form.modelName = providerConfig.models[0];
      } else {
        this.form.modelName = "";
      }
      if (this.form.llmProvider !== "gemini") {
        this.form.customEndpoint = "";
        this.form.customApiAuth = "bearer";
      }
    },
    toLocalInput(iso) {
      const d = new Date(iso);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    },
    fromLocalInput(local) {
      if (!local) {
        return new Date().toISOString();
      }
      return `${local}:00Z`;
    },
    onPromosUpload(event) {
      this.promosFile = event.target.files?.[0] || null;
    },
    onPricingUpload(event) {
      this.pricingFile = event.target.files?.[0] || null;
    },
    async runScan() {
      this.isRunning = true;
      this.showProgress = true;
      this.error = "";
      this.message = "";
      this.llmWarning = "";
      this.llmUsage = null;
      this.scanCompleted = false;
      try {
        const body = new FormData();
        body.append("channel", this.form.channel);
        body.append("as_of_utc", this.fromLocalInput(this.form.asOfUtc));
        body.append("horizon_days", String(this.form.horizonDays));
        body.append("use_samples", String(this.form.useSamples));
        body.append("use_llm_in_scan", String(this.form.useLlmInScan));
        body.append("llm_provider", this.form.llmProvider);
        body.append("api_key", this.form.apiKey);
        body.append("custom_endpoint", this.form.customEndpoint || "");
        body.append("custom_api_auth", this.form.customApiAuth || "bearer");
        body.append("model_name", this.form.modelName);
        body.append("max_llm_conflicts", String(this.form.maxLlmConflicts));
        if (this.promosFile) {
          body.append("promos_file", this.promosFile);
        }
        if (this.pricingFile) {
          body.append("pricing_file", this.pricingFile);
        }

        const resp = await fetch("/api/scan", { method: "POST", body });
        const payload = await resp.json();
        if (!resp.ok) {
          throw new Error(payload.detail || "Scan failed.");
        }
        this.conflicts = payload.conflicts || [];
        this.audit = payload.audit || {};
        this.message = payload.scan_message || "Scan complete.";
        this.llmWarning = payload.llm_warning || "";
        this.llmUsage = payload.llm_usage || null;
        if (payload.llm_used) {
          this.message += ` ${payload.llm_provider_used} explanations generated using ${payload.llm_model_used}.`;
        }
        this.renderSeverityChart();
        this.downloadPdf();
        this.scanCompleted = true;
      } catch (err) {
        this.error = err.message || "Unexpected error.";
        this.destroySeverityChart();
      } finally {
        this.isRunning = false;
      }
    },
    getSeverityCounts() {
      const counts = { CRITICAL: 0, WARNING: 0, INFO: 0 };
      for (const row of this.conflicts || []) {
        const sev = String(row?.severity || "INFO").toUpperCase();
        if (sev === "CRITICAL") counts.CRITICAL += 1;
        else if (sev === "WARNING") counts.WARNING += 1;
        else counts.INFO += 1;
      }
      return counts;
    },
    destroySeverityChart() {
      if (this.severityChart) {
        this.severityChart.destroy();
        this.severityChart = null;
      }
    },
    renderSeverityChart() {
      if (!window.Chart) return;
      this.$nextTick(() => {
        const canvas = document.getElementById("severityChart");
        if (!canvas) {
          this.destroySeverityChart();
          return;
        }
        const counts = this.getSeverityCounts();
        this.destroySeverityChart();
        this.severityChart = new window.Chart(canvas, {
          type: "bar",
          data: {
            labels: ["CRITICAL", "WARNING", "INFO"],
            datasets: [
              {
                label: "Conflicts",
                data: [counts.CRITICAL, counts.WARNING, counts.INFO],
                backgroundColor: ["#dc2626", "#d97706", "#2563eb"],
                borderRadius: 8,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
            },
            scales: {
              y: {
                beginAtZero: true,
                ticks: { precision: 0 },
              },
            },
          },
        });
      });
    },
    severityClass(severity) {
      if (severity === "CRITICAL") return "critical";
      if (severity === "WARNING") return "warning";
      return "info";
    },
    downloadPdf() {
      const jsPdfNS = window.jspdf;
      if (!jsPdfNS?.jsPDF) {
        return;
      }
      const doc = new jsPdfNS.jsPDF({ unit: "pt", format: "a4" });
      const pageWidth = doc.internal.pageSize.getWidth();
      const pageHeight = doc.internal.pageSize.getHeight();
      const margin = 40;
      const maxWidth = pageWidth - margin * 2;
      let y = margin;

      const ensureSpace = (needed) => {
        if (y + needed > pageHeight - margin) {
          doc.addPage();
          y = margin;
        }
      };

      const writeBlock = (text, fontSize = 11, gap = 16) => {
        doc.setFontSize(fontSize);
        const lines = doc.splitTextToSize(String(text || ""), maxWidth);
        const blockHeight = lines.length * (fontSize + 3);
        ensureSpace(blockHeight + gap);
        doc.text(lines, margin, y);
        y += blockHeight + gap;
      };

      doc.setFontSize(18);
      doc.setFont(undefined, "bold");
      doc.text("Promo Coherence Guard - Conflict Report", margin, y);
      y += 24;
      doc.setFont(undefined, "normal");
      writeBlock(`Generated at: ${new Date().toISOString()}`, 10, 10);
      writeBlock(`Total conflicts: ${this.conflicts.length}`, 10, 18);

      if (!this.conflicts.length) {
        writeBlock("No conflicts detected for the selected channel and as-of time.", 12, 18);
      } else {
        this.conflicts.forEach((row, idx) => {
          doc.setFont(undefined, "bold");
          writeBlock(
            `${idx + 1}. [${row.severity || "INFO"}] ${row.conflict_type || "Conflict"} - SKU ${row.sku || "-"}`,
            12,
            8
          );
          doc.setFont(undefined, "normal");
          writeBlock(row.deterministic_summary || "", 11, 8);
          if (row.llm_narrative) {
            writeBlock(`LLM narrative: ${row.llm_narrative}`, 10, 10);
          }
          writeBlock(`Promo IDs: ${row.promo_ids || "-"}`, 10, 14);
        });
      }

      if (this.audit && Object.keys(this.audit).length) {
        doc.setFont(undefined, "bold");
        writeBlock("Audit metadata", 12, 8);
        doc.setFont(undefined, "normal");
        writeBlock(JSON.stringify(this.audit, null, 2), 9, 8);
      }

      const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
      doc.save(`promo_coherence_report_${timestamp}.pdf`);
    },
    applyTheme() {
      const theme = this.isLightTheme ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("pcg-theme", theme);
    },
  },
  async mounted() {
    try {
      const savedTheme = localStorage.getItem("pcg-theme");
      this.isLightTheme = savedTheme === "light";
      this.applyTheme();
      await this.loadConfig();
    } catch (err) {
      this.error = "Failed to load UI configuration.";
    }
  },
}).mount("#app");
