(() => {
  const today = document.getElementById('today-label');
  const now = new Date();
  if (today) today.textContent = new Intl.DateTimeFormat('es-ES', {day: '2-digit', month: 'short', year: 'numeric'}).format(now);
  const healthTime = document.getElementById('health-check-time');
  if (healthTime) healthTime.textContent = new Intl.DateTimeFormat('es-ES', {hour: '2-digit', minute: '2-digit'}).format(now);
  const menu = document.getElementById('mobile-menu');
  const sidebar = document.getElementById('app-sidebar');
  if (menu && sidebar) {
    menu.addEventListener('click', () => {
      const open = sidebar.classList.toggle('is-open');
      menu.setAttribute('aria-expanded', String(open));
      menu.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú');
    });
  }
})();

