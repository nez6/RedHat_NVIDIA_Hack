// Shared top navigation. Tabs follow the signed-in role (app/rbac.py): admin = tester UI + both workspaces,
// realtor / client = their workspace plus the tools their role may open. Signed-out visitors see the public pages.
(function () {
  var pages = [
    ["start", "Home Match Toolkit", "start"], ["app", "Find a home", "app"], ["value", "Free value calculator", "value"],
    ["how", "How we score", "how"], ["pipeline", "Data flow", "pipeline"], ["testlab", "Agent test lab", "testlab"], ["architecture", "Architecture", "architecture"],
    ["./?view=realtor", "Realtor workspace", "realtor_view"], ["./?view=client", "Client portal", "client_view"]
  ];
  var here = location.pathname.replace(/\/+$/, "").split("/").pop();
  var nav = document.createElement("nav");
  nav.className = "topnav";
  document.body.insertBefore(nav, document.body.firstChild);

  function esc(s) { return String(s || "").replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function render(me) {
    var allowed = me && me.signed_in ? me.pages : ["start", "value"];
    var html = '<a class="brand" href="./">EstatePulse AI</a>' + pages.filter(function (p) { return allowed.indexOf(p[2]) >= 0; }).map(function (p) {
      return '<a class="tab' + (p[0] === here ? " on" : "") + '" href="' + p[0] + '">' + p[1] + "</a>";
    }).join("");
    if (me && me.signed_in) {
      if (me.role !== "admin") html = html.replace('href="./">EstatePulse AI', 'href="./">EstatePulse AI · ' + (me.role === "realtor" ? "Realtor" : "My journey"));
      html += '<span class="tab" style="margin-left:auto;cursor:default">' + esc(me.label) + " · " + esc(me.email) + '</span><a class="tab" href="#" id="ep-logout">Log out</a>';
    } else {
      html += '<a class="tab" style="margin-left:auto" href="./?login=1">Sign in</a>';
    }
    nav.innerHTML = html;
    var lo = document.getElementById("ep-logout");
    if (lo) lo.onclick = function (e) { e.preventDefault(); fetch("api/logout", {method: "POST"}).then(function () { location.href = "./"; }); };
  }
  render(null);
  fetch("api/me").then(function (r) { return r.json(); }).then(function (me) { window.EP_ME = me; render(me); }).catch(function () {});
})();
