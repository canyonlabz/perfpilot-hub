# Performance Test Comparison Report
**Generated:** {{GENERATED_TIMESTAMP}}  
**Test Runs Analyzed:** {{RUN_COUNT}}  
**Environment:** {{ENVIRONMENT}}  
**MCP Version:** {{MCP_VERSION}}

> **Note:** This report compares {{RUN_COUNT}} test runs. For optimal readability, a maximum of 5 runs is recommended. Comparing more than 5 runs may reduce report clarity for stakeholders.

---

## 1.0 Executive Summary

{{EXECUTIVE_SUMMARY}}

### 1.1 Key Findings
{{KEY_FINDINGS_BULLETS}}

### 1.2 Overall Performance Trend
{{OVERALL_TREND_SUMMARY}}

---

## 2.0 Test Configurations

| Configuration Item | {{RUN_1_LABEL}} | {{RUN_2_LABEL}} | {{RUN_3_LABEL}} | {{RUN_4_LABEL}} | {{RUN_5_LABEL}} |
|--------------------|-----------------|-----------------|-----------------|-----------------|-----------------|
| **Test Run ID** | {{RUN_1_ID}} | {{RUN_2_ID}} | {{RUN_3_ID}} | {{RUN_4_ID}} | {{RUN_5_ID}} |
| **Test Date** | {{RUN_1_DATE}} | {{RUN_2_DATE}} | {{RUN_3_DATE}} | {{RUN_4_DATE}} | {{RUN_5_DATE}} |
| **Start Time** | {{RUN_1_START_TIME}} | {{RUN_2_START_TIME}} | {{RUN_3_START_TIME}} | {{RUN_4_START_TIME}} | {{RUN_5_START_TIME}} |
| **End Time** | {{RUN_1_END_TIME}} | {{RUN_2_END_TIME}} | {{RUN_3_END_TIME}} | {{RUN_4_END_TIME}} | {{RUN_5_END_TIME}} |
| **Max Virtual Users** | {{RUN_1_MAX_VU}} | {{RUN_2_MAX_VU}} | {{RUN_3_MAX_VU}} | {{RUN_4_MAX_VU}} | {{RUN_5_MAX_VU}} |
| **Test Duration** | {{RUN_1_DURATION}} | {{RUN_2_DURATION}} | {{RUN_3_DURATION}} | {{RUN_4_DURATION}} | {{RUN_5_DURATION}} |
| **Total Samples** | {{RUN_1_SAMPLES}} | {{RUN_2_SAMPLES}} | {{RUN_3_SAMPLES}} | {{RUN_4_SAMPLES}} | {{RUN_5_SAMPLES}} |
| **Success Rate** | {{RUN_1_SUCCESS_RATE}}% | {{RUN_2_SUCCESS_RATE}}% | {{RUN_3_SUCCESS_RATE}}% | {{RUN_4_SUCCESS_RATE}}% | {{RUN_5_SUCCESS_RATE}}% |
| **Environment** | {{RUN_1_ENV}} | {{RUN_2_ENV}} | {{RUN_3_ENV}} | {{RUN_4_ENV}} | {{RUN_5_ENV}} |
| **Test Type** | {{RUN_1_TYPE}} | {{RUN_2_TYPE}} | {{RUN_3_TYPE}} | {{RUN_4_TYPE}} | {{RUN_5_TYPE}} |

---

## 3.0 Issues Observed

{{ISSUES_SUMMARY}}

### 3.1 Critical Issues

{{CRITICAL_ISSUES_TABLE}}

### 3.2 Performance Degradations

| Issue Category | Severity | Affected Runs | Description |
|----------------|----------|---------------|-------------|
{{PERFORMANCE_DEGRADATIONS_ROWS}}

### 3.3 Infrastructure Concerns

| Concern | Resource Type | Affected Runs | Details |
|---------|--------------|---------------|---------|
{{INFRASTRUCTURE_CONCERNS_ROWS}}

### 3.4 Error Rate Comparison

| Run | Total Errors | Error Rate (%) | Δ vs Previous Run | Top Error Type |
|-----|--------------|----------------|-------------------|----------------|
| {{RUN_1_LABEL}} | {{RUN_1_ERROR_COUNT}} | {{RUN_1_ERROR_RATE}} | - | {{RUN_1_TOP_ERROR}} |
| {{RUN_2_LABEL}} | {{RUN_2_ERROR_COUNT}} | {{RUN_2_ERROR_RATE}} | {{RUN_2_ERROR_DELTA}} | {{RUN_2_TOP_ERROR}} |
| {{RUN_3_LABEL}} | {{RUN_3_ERROR_COUNT}} | {{RUN_3_ERROR_RATE}} | {{RUN_3_ERROR_DELTA}} | {{RUN_3_TOP_ERROR}} |
| {{RUN_4_LABEL}} | {{RUN_4_ERROR_COUNT}} | {{RUN_4_ERROR_RATE}} | {{RUN_4_ERROR_DELTA}} | {{RUN_4_TOP_ERROR}} |
| {{RUN_5_LABEL}} | {{RUN_5_ERROR_COUNT}} | {{RUN_5_ERROR_RATE}} | {{RUN_5_ERROR_DELTA}} | {{RUN_5_TOP_ERROR}} |

**Summary:** {{ERROR_RATE_SUMMARY}}

### 3.5 Bugs Created Per Run

| Run | Bug ID | Bug Title | Status | Severity | Link |
|-----|--------|-----------|--------|----------|------|
| {{RUN_1_LABEL}} | {{RUN_1_BUG_1_ID}} | {{RUN_1_BUG_1_TITLE}} | {{RUN_1_BUG_1_STATUS}} | {{RUN_1_BUG_1_SEVERITY}} | [View]({{RUN_1_BUG_1_LINK}}) |
| {{RUN_1_LABEL}} | {{RUN_1_BUG_2_ID}} | {{RUN_1_BUG_2_TITLE}} | {{RUN_1_BUG_2_STATUS}} | {{RUN_1_BUG_2_SEVERITY}} | [View]({{RUN_1_BUG_2_LINK}}) |
| {{RUN_2_LABEL}} | {{RUN_2_BUG_1_ID}} | {{RUN_2_BUG_1_TITLE}} | {{RUN_2_BUG_1_STATUS}} | {{RUN_2_BUG_1_SEVERITY}} | [View]({{RUN_2_BUG_1_LINK}}) |
| ... | ... | ... | ... | ... | ... |

**Total Bugs:** {{TOTAL_BUG_COUNT}}  
**Open Bugs:** {{OPEN_BUG_COUNT}}  
**Resolved Bugs:** {{RESOLVED_BUG_COUNT}}

---

## 4.0 API Performance Comparison

**Focus:** APIs exceeding SLA thresholds or showing significant regression across test runs

### 4.1 SLA Violations by Run

| API Name | SLA Threshold (ms) | {{RUN_1_LABEL}} | {{RUN_2_LABEL}} | {{RUN_3_LABEL}} | {{RUN_4_LABEL}} | {{RUN_5_LABEL}} | Trend |
|----------|-------------------|-----------------|-----------------|-----------------|-----------------|-----------------|-------|
{{API_COMPARISON_ROWS}}

**Legend:**
- ✅ Met SLA
- ❌ Exceeded SLA
- ⬆️ **Improved** (response time decreased)
- ⬇️ **Degraded** (response time increased)
- ➡️ **Stable** (±5% or less)

### 4.2 90th Percentile Response Time by API

**P90 response times across runs (lower is better):**

{{P90_COMPARISON_ROWS}}

> **Note:** P90 (90th percentile) means 90% of requests completed faster than this time. This metric is more representative of user experience than average response time.

### 4.3 Top Response Time Offenders

**APIs with consistently high response times across runs:**

| API Name | {{RUN_1_LABEL}} (ms) | {{RUN_2_LABEL}} (ms) | {{RUN_3_LABEL}} (ms) | {{RUN_4_LABEL}} (ms) | {{RUN_5_LABEL}} (ms) | Avg Δ |
|----------|---------------------|---------------------|---------------------|---------------------|---------------------|-------|
{{TOP_OFFENDERS_ROWS}}

### 4.4 Throughput Comparison

| Metric | {{RUN_1_LABEL}} | {{RUN_2_LABEL}} | {{RUN_3_LABEL}} | {{RUN_4_LABEL}} | {{RUN_5_LABEL}} | Trend |
|--------|-----------------|-----------------|-----------------|-----------------|-----------------|-------|
| **Avg Throughput (req/sec)** | {{RUN_1_AVG_THROUGHPUT}} | {{RUN_2_AVG_THROUGHPUT}} | {{RUN_3_AVG_THROUGHPUT}} | {{RUN_4_AVG_THROUGHPUT}} | {{RUN_5_AVG_THROUGHPUT}} | {{THROUGHPUT_TREND}} |
| **Peak Throughput (req/sec)** | {{RUN_1_PEAK_THROUGHPUT}} | {{RUN_2_PEAK_THROUGHPUT}} | {{RUN_3_PEAK_THROUGHPUT}} | {{RUN_4_PEAK_THROUGHPUT}} | {{RUN_5_PEAK_THROUGHPUT}} | {{PEAK_THROUGHPUT_TREND}} |

**Summary:** {{THROUGHPUT_SUMMARY}}

---

## 5.0 Infrastructure Metrics Comparison

### 5.1 CPU Utilization by {{INFRA_ENTITY_TYPE}}

| {{INFRA_ENTITY_TYPE}} Name | {{RUN_1_LABEL}} | {{RUN_2_LABEL}} | {{RUN_3_LABEL}} | {{RUN_4_LABEL}} | {{RUN_5_LABEL}} | Trend | Δ vs Run 1 |
|--------------|-----------------|-----------------|-----------------|-----------------|-----------------|-------|------------|
{{CPU_COMPARISON_ROWS}}

**Summary:**
- **Improved:** {{CPU_IMPROVED_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ⬆️
- **Degraded:** {{CPU_DEGRADED_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ⬇️
- **Stable:** {{CPU_STABLE_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ➡️

#### 5.1.1 CPU Core Usage by {{INFRA_ENTITY_TYPE}}

{{CPU_CORE_COMPARISON_ROWS}}

> **Note:** Peak and Average values show actual CPU cores consumed. mCPU = millicores (1 core = 1000 mCPU). For host-based environments, CPU core values may not be available from Datadog metrics.

#### 5.1.2 Peak CPU Core Usage Comparison Charts

{{CHART_PLACEHOLDER: CPU_PEAK_CORE_COMPARISON_BAR}}

#### 5.1.3 Average CPU Core Usage Comparison Charts

{{CHART_PLACEHOLDER: CPU_AVG_CORE_COMPARISON_BAR}}

### 5.2 Memory Utilization by {{INFRA_ENTITY_TYPE}}

| {{INFRA_ENTITY_TYPE}} Name | {{RUN_1_LABEL}} | {{RUN_2_LABEL}} | {{RUN_3_LABEL}} | {{RUN_4_LABEL}} | {{RUN_5_LABEL}} | Trend | Δ vs Run 1 |
|--------------|-----------------|-----------------|-----------------|-----------------|-----------------|-------|------------|
{{MEMORY_COMPARISON_ROWS}}

**Summary:**
- **Improved:** {{MEMORY_IMPROVED_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ⬆️
- **Degraded:** {{MEMORY_DEGRADED_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ⬇️
- **Stable:** {{MEMORY_STABLE_COUNT}} {{INFRA_ENTITY_TYPE_LOWER}}(s) ➡️

#### 5.2.1 Memory Usage by {{INFRA_ENTITY_TYPE}}

{{MEMORY_USAGE_COMPARISON_ROWS}}

> **Note:** Peak and Average values show actual memory consumed. MB = Megabytes (1 GB = 1024 MB).

#### 5.2.2 Peak Memory Usage Comparison Charts

{{CHART_PLACEHOLDER: MEMORY_PEAK_USAGE_COMPARISON_BAR}}

#### 5.2.3 Average Memory Usage Comparison Charts

{{CHART_PLACEHOLDER: MEMORY_AVG_USAGE_COMPARISON_BAR}}

### 5.3 Resource Efficiency Assessment

{{RESOURCE_EFFICIENCY_SUMMARY}}

---

## 6.0 Correlation Insights (Optional)

{{CORRELATION_INSIGHTS_SECTION}}

**Key Observations:**
{{CORRELATION_KEY_OBSERVATIONS}}

> **Note:** This section is included only when significant performance-infrastructure correlations are detected (e.g., high CPU causing response time spikes).

---

## 7.0 Conclusion

### 7.1 Synopsis

{{CONCLUSION_SYNOPSIS}}

### 7.2 Recommendations

{{RECOMMENDATIONS_LIST}}

### 7.3 Next Steps

{{NEXT_STEPS_LIST}}

---

## Appendix A: Test Run Metadata

### Source Files by Run

**{{RUN_1_LABEL}} ({{RUN_1_ID}})**
{{RUN_1_SOURCE_FILES}}

**{{RUN_2_LABEL}} ({{RUN_2_ID}})**
{{RUN_2_SOURCE_FILES}}

**{{RUN_3_LABEL}} ({{RUN_3_ID}})**
{{RUN_3_SOURCE_FILES}}

**{{RUN_4_LABEL}} ({{RUN_4_ID}})**
{{RUN_4_SOURCE_FILES}}

**{{RUN_5_LABEL}} ({{RUN_5_ID}})**
{{RUN_5_SOURCE_FILES}}

### Report Generation Details
- **Report Generated By:** PerfReport MCP Server
- **MCP Version:** {{MCP_VERSION}}
- **Generation Timestamp:** {{GENERATED_TIMESTAMP}}
- **Runs Compared:** {{RUN_IDS_LIST}}
- **Comparison Method:** Sequential run-to-run analysis

---

*End of Comparison Report*
