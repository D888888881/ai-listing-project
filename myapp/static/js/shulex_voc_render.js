/**
 * VOC Shulex 卡片模板（消费者画像 + 五表），支持 bundle（对标 + 集群）与区块「展开」全量表。
 */
(function (global) {
  function escHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function vocDataIsEmpty(v) {
    if (v == null) return true;
    if (typeof v === "string") return !String(v).trim();
    if (Array.isArray(v)) return v.length === 0;
    if (typeof v === "object") return Object.keys(v).length === 0;
    return false;
  }

  function barClassBySection(title) {
    const t = String(title);
    if (t.includes("使用场景")) return "slxBarBlue";
    if (t.includes("未被满足")) return "slxBarOrange";
    if (t === "好评") return "slxBarGreen";
    if (t === "差评") return "slxBarRed";
    if (t.includes("购买动机")) return "slxBarYellow";
    return "slxBarGreen";
  }

  function renderFullVocSectionTable(sectionTitle, sectionRows, barClass) {
    const rows = (Array.isArray(sectionRows) ? sectionRows : [])
      .map((row) => {
        const desc = row?.描述 ?? row?.desc ?? "";
        const percent = row?.占比 ?? row?.percent ?? "";
        const reason = row?.原因 ?? row?.reason ?? "";
        const m = percent && String(percent).match(/(\d+(?:\.\d+)?)%/);
        const pNum = m ? Math.max(0, Math.min(100, parseFloat(m[1]))) : null;
        const bar =
          pNum === null || isNaN(pNum)
            ? ""
            : `
              <div class="shulexVocSSTablePercentBar ${barClass}">
                <div class="shulexVocSSTablePercentLine" style="width:${pNum}%;"></div>
              </div>
            `.trim();
        return `
              <div class="shulexVocSSTableRow" style="grid-template-columns: minmax(0, 30%) minmax(0, 10%) 1fr;">
                <div class="shulexVocSSTableCell" title="${escHtml(desc)}">${escHtml(String(desc))}</div>
                <div class="shulexVocSSTableCell">
                  <div class="shulexVocSSTablePercent">
                    <div class="shulexVocSSTablePercentNum">${escHtml(String(percent))}</div>
                    ${bar}
                  </div>
                </div>
                <div class="shulexVocSSTableCell slxModalReasonWrap">${escHtml(String(reason))}</div>
              </div>
            `.trim();
      })
      .join("");

    return `
            <div class="shulexVocSSBodyCardList" style="grid-template-columns: 1fr;">
              <div class="shulexVocSSBodyCard">
                <div class="shulexVocSSBodyCardHead"><span>${escHtml(sectionTitle)}</span></div>
                <div class="shulexVocSSTable">
                  <div class="shulexVocSSTableHead">
                    <div class="shulexVocSSTableRow" style="grid-template-columns: minmax(0, 30%) minmax(0, 10%) 1fr;">
                      <div class="shulexVocSSTableCell">描述</div>
                      <div class="shulexVocSSTableCell">占比</div>
                      <div class="shulexVocSSTableCell">原因</div>
                    </div>
                  </div>
                  <div class="shulexVocSSTableList">${
                    rows || `<div style="padding:12px 0; color:#86868b;">暂无</div>`
                  }</div>
                </div>
              </div>
            </div>
          `.trim();
  }

  function findSectionRows(vocObj, sectionTitle) {
    if (!vocObj || typeof vocObj !== "object") return [];
    return vocObj[sectionTitle] || [];
  }

  /**
   * @param {HTMLElement} modalBody
   * @param {HTMLElement} titleEl
   * @param {{ stack: Array<{title:string,html:string,rawVoc?:any}>; getFallbackRawVoc?: () => any }} opts
   */
  function installVocExpandForModal(modalBody, titleEl, opts) {
    const stack = opts && opts.stack;
    const getFallbackRawVoc = opts && typeof opts.getFallbackRawVoc === "function" ? opts.getFallbackRawVoc : null;
    if (!modalBody || !stack || modalBody.dataset.vocExpandInstall === "1") return;
    modalBody.dataset.vocExpandInstall = "1";

    modalBody.addEventListener("click", function (e) {
      const btn = e.target.closest(".slx-voc-section-expand");
      if (!btn) return;
      e.stopPropagation();
      const sectionTitle = btn.getAttribute("data-section");
      if (!sectionTitle) return;
      const clusterKey = btn.getAttribute("data-bundle-cluster") || "";
      let rawVoc = modalBody.__vocRawData;
      if ((!rawVoc || typeof rawVoc !== "object") && getFallbackRawVoc) {
        try {
          rawVoc = getFallbackRawVoc() || {};
        } catch (err) {
          rawVoc = {};
        }
      }
      if (!rawVoc || typeof rawVoc !== "object") return;
      let vocObj = rawVoc;
      if (rawVoc._voc_bundle) {
        vocObj = clusterKey ? rawVoc.cluster && rawVoc.cluster[clusterKey] : rawVoc.target;
      }
      if (!vocObj || typeof vocObj !== "object") vocObj = {};
      const rows = findSectionRows(vocObj, sectionTitle);
      stack.push({
        title: titleEl.textContent,
        html: modalBody.innerHTML,
        rawVoc: modalBody.__vocRawData,
      });
      const hint = (titleEl.textContent || "").replace(/^.*[—\-]\s*/, "").trim() || "VOC";
      titleEl.textContent = `${sectionTitle} — ${hint}`;
      modalBody.innerHTML = renderFullVocSectionTable(
        sectionTitle,
        rows,
        barClassBySection(sectionTitle)
      );
    });
  }

  function renderShulexVocCardsOnly(voc, options) {
    const embedFull = options && options.embedFull === true;
    const clusterKey = options && options.clusterKey ? String(options.clusterKey) : "";
    const SLX_EXPAND_SVG = `
          <svg class="slx_table_expand" width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M5.833 1.75H1.75v4.083h4.083V1.75ZM12.25 1.75H8.167v4.083h4.083V1.75ZM12.25 8.167H8.167v4.083h4.083V8.167ZM5.833 8.167H1.75v4.083h4.083V8.167Z" stroke="#98A2B3" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path>
          </svg>
        `.trim();

    function esc(s) {
      return escHtml(s);
    }

    if (typeof voc === "string") return voc;
    if (voc && typeof voc === "object") {
      if (typeof voc.html === "string") return voc.html;
      if (typeof voc.voc_html === "string") return voc.voc_html;
      if (typeof voc.content === "string" && voc.content.includes("shulexVocSSBodyCardList"))
        return voc.content;
    }

    const isObj = voc && typeof voc === "object" && !Array.isArray(voc);
    const data = isObj ? voc : {};

    function renderListBlock(title, items) {
      const rows = (Array.isArray(items) ? items : [])
        .map((it) => {
          const name = esc(it?.name ?? it?.名称 ?? it?.描述 ?? "");
          const percent = esc(it?.percent ?? it?.占比 ?? "");
          return `
              <div class="shulexVocSSAspect">
                <span class="shulexVocSSAspectText" title="${name}">${name}</span>
                ${percent ? `<span title="（${percent}）">（${percent}）</span>` : `<span title=""></span>`}
              </div>
            `.trim();
        })
        .join("");
      return `
            <div class="shulexVocSSNerItem">
              <div class="shulexVocSSNerName">${esc(title)}</div>
              ${rows || `<div style="color:#86868b; font-size:0.85rem;">暂无</div>`}
            </div>
          `.trim();
    }

    function toNumPercent(p) {
      if (!p) return null;
      const m = String(p).match(/(\d+(?:\.\d+)?)%/);
      if (!m) return null;
      const v = Math.max(0, Math.min(100, parseFloat(m[1])));
      return isNaN(v) ? null : v;
    }

    function headIcon(type) {
      const map = {
        consumer: { bg: "#F0F9FF", stroke: "#0086C9" },
        usage: { bg: "#EEF4FF", stroke: "#444CE7" },
        unmet: { bg: "#FFF6ED", stroke: "#EC4A0A" },
        praise: { bg: "#ECFDF3", stroke: "#12B76A" },
        negative: { bg: "#FEF3F2", stroke: "#F97066" },
        motive: { bg: "#FFFAEB", stroke: "#DC6803" },
      };
      const c = map[type] || map.consumer;
      return `
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect width="20" height="20" rx="8" fill="${c.bg}"></rect>
              <path d="M6 13.5h8M6 10h8M6 6.5h5" stroke="${c.stroke}" stroke-width="1.6" stroke-linecap="round"/>
            </svg>
          `.trim();
    }

    function renderTableCard(title, arr, barClass, iconType) {
      const allRows = Array.isArray(arr) ? arr : [];
      const previewRows = embedFull ? allRows : allRows.slice(0, 5);
      const list = previewRows
        .map((row) => {
          const desc = esc(row?.描述 ?? row?.desc ?? "");
          const percent = esc(row?.占比 ?? row?.percent ?? "");
          const reason = esc(row?.原因 ?? row?.reason ?? "");
          const pNum = toNumPercent(percent);
          const bar =
            pNum === null
              ? ""
              : `
              <div class="shulexVocSSTablePercentBar ${barClass}">
                <div class="shulexVocSSTablePercentLine" style="width:${pNum}%;"></div>
              </div>
            `.trim();
          return `
              <div class="shulexVocSSTableRow">
                <div class="shulexVocSSTableCell" title="${desc}">${desc}</div>
                <div class="shulexVocSSTableCell">
                  <div class="shulexVocSSTablePercent">
                    <div class="shulexVocSSTablePercentNum">${percent}</div>
                    ${bar}
                  </div>
                </div>
                <div class="shulexVocSSTableCell shulexVocSSTableLastCell" title="${reason}">${reason}</div>
              </div>
            `.trim();
        })
        .join("");

      const clusterAttr = clusterKey
        ? ` data-bundle-cluster="${escHtml(clusterKey)}"`
        : "";

      const headInner = embedFull
        ? `
                <div class="shulexVocSSTableRow">
                    <div class="shulexVocSSTableCell">描述</div>
                    <div class="shulexVocSSTableCell">占比</div>
                    <div class="shulexVocSSTableCell">原因</div>
                </div>
              `
        : `
                <div class="shulexVocSSTableRow">
                    <div class="shulexVocSSTableCell">描述</div>
                    <div class="shulexVocSSTableCell">占比</div>
                    <div class="shulexVocSSTableCell">原因</div>
                    <div class="shulexVocSSTableCell" style="text-align:right;">
                      <button type="button" class="slx_table_expand_btn slx-voc-section-expand"
                              data-section="${esc(title)}"${clusterAttr} aria-label="展开查看全部">${SLX_EXPAND_SVG}</button>
                    </div>
                </div>
              `;

      return `
            <div class="shulexVocSSBodyCard">
              <div class="shulexVocSSBodyCardHead">
                ${headIcon(iconType)}<span>${esc(title)}</span>

              </div>
              <div class="shulexVocSSTable">
                <div class="shulexVocSSTableHead">
                  ${headInner}
                </div>
                <div class="shulexVocSSTableList">
                  ${list || `<div style="padding:12px 0; color:#86868b;">暂无</div>`}
                </div>
              </div>
            </div>
          `.trim();
    }

    const profile = data["消费者画像"] ?? data["consumer_profile"] ?? null;
    let profileCard = "";
    if (profile && typeof profile === "object") {
      const male = esc(profile?.性别?.男性 ?? profile?.gender?.male ?? "");
      const female = esc(profile?.性别?.女性 ?? profile?.gender?.female ?? "");
      profileCard = `
            <div class="shulexVocSSBodyCard">
              <div class="shulexVocSSBodyCardHead">${headIcon("consumer")}<span>消费者画像</span></div>
              <div class="shulexVocSSCardBodyNer">
                <div class="shulexVocSSNerSexPercent" style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                  <span class="shulexVocSSNerSexNum">${male || ""}</span>
                  <div style="flex:1; height:10px; background:#eaecf0; border-radius:999px; overflow:hidden;">
                    <div style="height:100%; width:${male || "0%"}; background:#1570EF;"></div>
                  </div>
                  <span class="shulexVocSSNerSexNum">${female || ""}</span>
                  <div style="flex:1; height:10px; background:#eaecf0; border-radius:999px; overflow:hidden;">
                    <div style="height:100%; width:${female || "0%"}; background:#DD2590;"></div>
                  </div>
                </div>
                <svg width="100%" height="100%" viewBox="0 0 470 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="shulexVocSSNerLine">
                  <path stroke="#EAECF0" d="M469 12v8M313 12v8M157 12v8M1 12v8"></path>
                  <path stroke="#EAECF0" stroke-linecap="square" d="M1 11.5h468"></path>
                  <path stroke="#EAECF0" d="M234.5 0v12"></path>
                </svg>
                <div class="shulexVocSSNerBox">
                  ${renderListBlock("人群特征", profile["人群特征"])}
                  ${renderListBlock("使用时刻", profile["使用时刻"])}
                  ${renderListBlock("使用地点", profile["使用地点"])}
                  ${renderListBlock("行为", profile["行为"])}
                </div>
              </div>
            </div>
          `.trim();
    }

    const cards = [];
    if (profileCard) cards.push(profileCard);
    cards.push(renderTableCard("使用场景", data["使用场景"], "slxBarBlue", "usage"));
    cards.push(renderTableCard("未被满足的需求", data["未被满足的需求"], "slxBarOrange", "unmet"));
    cards.push(renderTableCard("好评", data["好评"], "slxBarGreen", "praise"));
    cards.push(renderTableCard("差评", data["差评"], "slxBarRed", "negative"));
    cards.push(renderTableCard("购买动机", data["购买动机"], "slxBarYellow", "motive"));

    const shouldFallback = !profileCard && Object.keys(data || {}).length === 0;
    if (shouldFallback) {
      const pretty = esc(JSON.stringify(voc, null, 2));
      return `
            <div class="shulexVocSSBodyCardList${embedFull ? " voc-embed-full" : ""}">
              <div class="shulexVocSSBodyCard">
                <div class="shulexVocSSBodyCardHead"><span>VOC 原始数据</span></div>
                <pre class="voc-pre">${pretty}</pre>
              </div>
            </div>
          `.trim();
    }

    return `<div class="shulexVocSSBodyCardList${embedFull ? " voc-embed-full" : ""}">${cards.join(
      ""
    )}</div>`;
  }

  function renderVocBundleHtml(bundle, options) {
    const innerOpts = { embedFull: false, ...(options || {}) };
    innerOpts.embedFull = false;

    function panel(cls, headLeft, headRight, bodyHtml) {
      const right = headRight ? `<div class="voc-bundle-badges">${headRight}</div>` : "";
      return `
            <div class="voc-bundle-panel ${cls}">
              <div class="voc-bundle-panel-head">
                <span>${headLeft}</span>
                ${right}
              </div>
              <div class="voc-bundle-panel-body">${bodyHtml}</div>
            </div>
          `.trim();
    }
    const targetInner = vocDataIsEmpty(bundle.target)
      ? '<p class="voc-bundle-empty">暂无对标 VOC。请在「原文本」页为该 ASIN 导入 VOC JSON（如 B0XXXXXXXXXX_VOC.json）。</p>'
      : `<div class="voc-bundle-shulex-inner">${renderShulexVocCardsOnly(bundle.target, {
          embedFull: false,
        })}</div>`;
    const targetHead = '<span class="voc-bundle-asin">对标 · ' + escHtml(bundle.row_asin || "") + "</span>";
    let html = '<div class="voc-bundle-wrap">';
    html += panel(
      "voc-bundle-panel--target",
      targetHead,
      '<span class="voc-bundle-badge" style="background:#E0F2FE;color:#0369A1;border:1px solid #7CD4FD;">主 VOC</span>',
      targetInner
    );

    const meta = bundle.cluster_meta || {};
    const cluster = bundle.cluster || {};
    const keys = Object.keys(cluster).sort();
    if (!keys.length) {
      html += panel(
        "voc-bundle-panel--cluster",
        "<span>ASIN 集群</span>",
        "",
        '<p class="voc-bundle-empty">暂无集群 ASIN。请在「原文本」配置 ASIN 集群或导入 VOC 集群。</p>'
      );
    } else {
      keys.forEach((k) => {
        const v = cluster[k];
        const m = meta[k] || "empty";
        let badge = "";
        if (m === "voc_cluster") {
          badge =
            '<span class="voc-bundle-badge voc-bundle-badge--cluster" title="通过「导入 VOC 集群」写入">VOC 集群导入</span>';
        } else if (m === "original_row") {
          badge =
            '<span class="voc-bundle-badge voc-bundle-badge--row" title="读取自该 ASIN 在原文本列表中的独立行">独立行 VOC</span>';
        } else {
          badge = '<span class="voc-bundle-badge voc-bundle-badge--empty">无数据</span>';
        }
        const head = '<span class="voc-bundle-asin">集群 · ' + escHtml(k) + "</span>";
        const body = vocDataIsEmpty(v)
          ? '<p class="voc-bundle-empty">无 VOC。请导入 VOC 集群或为该集群 ASIN 维护原文本行。</p>'
          : `<div class="voc-bundle-shulex-inner">${renderShulexVocCardsOnly(v, {
              embedFull: false,
              clusterKey: k,
            })}</div>`;
        html += panel("voc-bundle-panel--cluster", head, badge, body);
      });
    }
    html += "</div>";
    return html;
  }

  function renderShulexVocHtml(voc, options) {
    if (voc && typeof voc === "object" && voc._voc_bundle) {
      return renderVocBundleHtml(voc, options);
    }
    const o = options || {};
    if (o.embedFull !== true) {
      o.embedFull = false;
    }
    return renderShulexVocCardsOnly(voc, o);
  }

  global.renderShulexVocHtml = renderShulexVocHtml;
  global.renderVocBundleHtml = renderVocBundleHtml;
  global.renderShulexVocCardsOnly = renderShulexVocCardsOnly;
  global.renderFullVocSectionTable = renderFullVocSectionTable;
  global.installVocExpandForModal = installVocExpandForModal;
})(typeof window !== "undefined" ? window : this);
