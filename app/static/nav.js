// Shared top navigation for every page. Add a page here once and it shows up everywhere.
(function () {
  var pages = [["./", "Find a home"], ["value", "Free value calculator"], ["how", "How we score"], ["architecture", "Architecture"]];
  var here = location.pathname.replace(/\/+$/, "").split("/").pop();
  var html = '<a class="brand" href="./">EstatePulse AI</a>' + pages.map(function (p) {
    var key = p[0] === "./" ? "" : p[0];
    return '<a class="tab' + (key === here ? " on" : "") + '" href="' + p[0] + '">' + p[1] + "</a>";
  }).join("");
  var nav = document.createElement("nav");
  nav.className = "topnav";
  nav.innerHTML = html;
  document.body.insertBefore(nav, document.body.firstChild);
})();
