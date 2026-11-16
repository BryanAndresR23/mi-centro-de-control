/* Mi Centro de Control — comportamiento de las páginas (sin scripts en línea, por la CSP) */
(function () {
  "use strict";

  const metaCsrf = document.querySelector('meta[name="csrf-token"]');
  const csrf = metaCsrf ? metaCsrf.content : "";

  // HTMX envía el token CSRF en cada petición
  document.addEventListener("htmx:configRequest", (evento) => {
    if (csrf) evento.detail.headers["X-CSRF-Token"] = csrf;
  });

  // Confirmación antes de acciones destructivas: <form data-confirmar="¿Seguro?">
  document.addEventListener("submit", (evento) => {
    const mensaje = evento.target.dataset ? evento.target.dataset.confirmar : null;
    if (mensaje && !window.confirm(mensaje)) evento.preventDefault();
  });

  // --- Formulario de movimiento -------------------------------------------
  const formMov = document.querySelector("form.form-mov");
  if (formMov) {
    const actualizarTipo = () => {
      const elegido = formMov.querySelector('input[name="tipo"]:checked');
      if (elegido) formMov.dataset.tipo = elegido.value;
    };
    formMov.querySelectorAll('input[name="tipo"]').forEach((r) => r.addEventListener("change", actualizarTipo));
    actualizarTipo();

    const sincronizarChips = (select) => {
      formMov.querySelectorAll(`[data-campo="${select.name}"]`).forEach((boton) => {
        boton.classList.toggle("activo", boton.dataset.chipCategoria === select.value);
      });
    };
    formMov.querySelectorAll("[data-chip-categoria]").forEach((boton) => {
      boton.addEventListener("click", () => {
        const select = formMov.querySelector(`select[name="${boton.dataset.campo}"]`);
        if (!select) return;
        select.value = boton.dataset.chipCategoria;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });

    // Deducible: se sugiere según el rubro SRI de la categoría, salvo que lo toques a mano
    const deducible = formMov.querySelector('input[name="deducible"]');
    const pista = formMov.querySelector("[data-pista-rubro]");
    let deducibleTocado = formMov.dataset.editando === "1";
    if (deducible) deducible.addEventListener("change", () => { deducibleTocado = true; });

    const selGasto = formMov.querySelector('select[name="categoria_gasto"]');
    const selIngreso = formMov.querySelector('select[name="categoria_ingreso"]');
    const alCambiarGasto = (inicial) => {
      const opcion = selGasto.selectedOptions[0];
      const rubro = opcion ? opcion.dataset.rubro : "";
      if (deducible && !deducibleTocado && !inicial) deducible.checked = Boolean(rubro);
      if (pista) pista.textContent = rubro ? `Rubro SRI: ${opcion.dataset.rubroNombre}` : "";
      sincronizarChips(selGasto);
    };
    if (selGasto) {
      selGasto.addEventListener("change", () => alCambiarGasto(false));
      alCambiarGasto(true);
    }
    if (selIngreso) {
      selIngreso.addEventListener("change", () => sincronizarChips(selIngreso));
      sincronizarChips(selIngreso);
    }

    formMov.querySelectorAll("[data-fecha]").forEach((boton) => {
      boton.addEventListener("click", () => {
        const campo = formMov.querySelector('input[name="fecha"]');
        if (campo) campo.value = boton.dataset.fecha;
      });
    });
  }

  // --- Formulario de cuenta: campos de activo o de pasivo ----------------
  const formCuenta = document.querySelector("form.form-cuenta");
  if (formCuenta) {
    const tiposPasivo = (formCuenta.dataset.tiposPasivo || "").split(",");
    const select = formCuenta.querySelector('select[name="tipo"]');
    const actualizar = () => { formCuenta.dataset.pasivo = tiposPasivo.includes(select.value) ? "1" : "0"; };
    select.addEventListener("change", actualizar);
    actualizar();
  }

  // --- Formulario de categoría: el rubro SRI solo aplica a gastos --------
  const formCategoria = document.querySelector("form.form-categoria");
  if (formCategoria) {
    const select = formCategoria.querySelector('select[name="tipo"]');
    const actualizar = () => { formCategoria.dataset.tipo = select.value; };
    select.addEventListener("change", actualizar);
    actualizar();
  }

  // --- Gráficos del dashboard --------------------------------------------
  const nodoDatos = document.getElementById("datos-graficos");
  if (!nodoDatos || !window.Chart) return;
  const datos = JSON.parse(nodoDatos.textContent);
  const Chart = window.Chart;

  const css = (nombre) => getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();
  const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const enteroUSD = (v) => {
    const signo = v < 0 ? "−" : "";
    return `${signo}$${Math.round(Math.abs(v)).toLocaleString("en-US")}`;
  };
  const compactoUSD = (v) => {
    const a = Math.abs(v);
    const signo = v < 0 ? "−" : "";
    if (a >= 1e6) return `${signo}$${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${signo}$${Math.round(a / 1e3)}K`;
    return `${signo}$${Math.round(a)}`;
  };
  // Ticks redondos: completos bajo 10,000; compactos si el eje llega a 10K (un estilo por eje)
  function tickY(valor) {
    const max = Math.max(Math.abs(this.max), Math.abs(this.min));
    return max >= 10000 ? compactoUSD(valor) : enteroUSD(valor);
  }

  // Línea vertical (cruz) en la posición del tooltip
  const cruz = {
    id: "cruz",
    afterDatasetsDraw(grafico) {
      const activos = grafico.tooltip && grafico.tooltip.getActiveElements();
      if (!activos || !activos.length) return;
      const x = activos[0].element.x;
      const { top, bottom } = grafico.chartArea;
      const ctx = grafico.ctx;
      ctx.save();
      ctx.strokeStyle = css("--eje");
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  let graficos = [];

  function serie(etiqueta, valores, color, superficie) {
    const ultimo = valores.length - 1;
    return {
      label: etiqueta,
      data: valores,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      borderCapStyle: "round",
      borderJoinStyle: "round",
      tension: 0.25,
      // Solo el punto final lleva marcador (r = 4, con anillo del color de la superficie)
      pointRadius: (ctx) => (ctx.dataIndex === ultimo ? 4 : 0),
      pointHoverRadius: 5,
      pointBorderColor: superficie,
      pointBorderWidth: 2,
      pointHoverBorderColor: superficie,
      pointHoverBorderWidth: 2,
      pointHitRadius: 12,
    };
  }

  function opciones(colores, conCero) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 6, right: 8 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: colores.texto,
          titleColor: colores.superficie,
          bodyColor: colores.superficie,
          padding: 10,
          cornerRadius: 8,
          boxWidth: 10,
          boxHeight: 10,
          boxPadding: 4,
          usePointStyle: true,
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${usd2.format(ctx.parsed.y)}` },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: colores.eje },
          ticks: { color: colores.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
        },
        y: {
          beginAtZero: conCero,
          grace: conCero ? 0 : "8%",
          grid: { color: colores.grid, lineWidth: 1 },
          border: { display: false },
          ticks: { color: colores.muted, maxTicksLimit: 5, callback: tickY },
        },
      },
    };
  }

  function dibujar() {
    graficos.forEach((g) => g.destroy());
    graficos = [];
    const colores = {
      s1: css("--serie-1"),
      s2: css("--serie-2"),
      grid: css("--grid"),
      eje: css("--eje"),
      muted: css("--texto-3"),
      texto: css("--texto"),
      superficie: css("--superficie"),
    };
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;

    const lienzoTendencia = document.getElementById("grafico-tendencia");
    if (lienzoTendencia) {
      graficos.push(
        new Chart(lienzoTendencia, {
          type: "line",
          data: {
            labels: datos.etiquetas,
            datasets: [
              serie("Ingresos", datos.ingresos, colores.s1, colores.superficie),
              serie("Gastos", datos.gastos, colores.s2, colores.superficie),
            ],
          },
          options: opciones(colores, true),
          plugins: [cruz],
        })
      );
    }

    const lienzoPatrimonio = document.getElementById("grafico-patrimonio");
    if (lienzoPatrimonio) {
      graficos.push(
        new Chart(lienzoPatrimonio, {
          type: "line",
          data: {
            labels: datos.etiquetas,
            datasets: [serie("Patrimonio", datos.patrimonio, colores.s1, colores.superficie)],
          },
          options: opciones(colores, false),
          plugins: [cruz],
        })
      );
    }
  }

  dibujar();
  // El modo oscuro tiene sus propios colores: se redibuja al cambiar
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", dibujar);
})();
