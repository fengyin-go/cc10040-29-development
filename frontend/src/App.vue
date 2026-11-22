<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">多维分析查询与指标平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">支撑多维分析查询、指标计算、口径管理和数据服务发布的数据应用平台</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "指标定义", path: "/metric_definition" }, { label: "维度建模", path: "/dimension_model" }, { label: "查询构建器", path: "/query_builder" }, { label: "数据服务", path: "/data_service" }, { label: "口径版本", path: "/caliber_version" }, { label: "查询日志", path: "/query_journal" }, { label: "下钻分析", path: "/drill_down" }, { label: "报告模板", path: "/report_template" }, { label: "实验分群", path: "/experiment_group" }, { label: "数据看板配置", path: "/data_dashboard" }, { label: "用户分群", path: "/user_cohort" }, { label: "异常检测器", path: "/anomaly_detector" }, { label: "批量巡检", path: "/anomaly_detector/batch-inspection" }, { label: "数据导出服务", path: "/data_export_service" }, { label: "分群对比分析", path: "/segment_compare" }, { label: "透视表配置", path: "/pivot_table" }, { label: "数据订阅", path: "/data_subscription" }, { label: "查询缓存层", path: "/cache_layer" }, { label: "数据追溯系统", path: "/trace_system" }, { label: "口径重算引擎", path: "/recalc_engine" }, { label: "字段血缘", path: "/field_lineage" }]
</script>
