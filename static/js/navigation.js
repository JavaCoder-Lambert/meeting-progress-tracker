document.addEventListener("DOMContentLoaded", () => {
  const navigation = document.querySelector("[data-nav-menu]");
  if (!navigation) return;
  const desktop = window.matchMedia("(min-width: 1040px)");
  const sync = () => { navigation.open = desktop.matches; };
  sync();
  desktop.addEventListener("change", sync);
});
