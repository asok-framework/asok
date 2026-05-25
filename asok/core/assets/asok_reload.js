(function () {
  let lastBuildId = "";
  setInterval(function () {
    fetch("/__reload")
      .then(function (r) {
        return r.text();
      })
      .then(function (buildId) {
        if (lastBuildId && lastBuildId !== buildId) {
          location.reload();
        }
        lastBuildId = buildId;
      })
      .catch(function () {
        lastBuildId = "";
      });
  }, 1000);
})();
