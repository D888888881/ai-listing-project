(function () {
  function goToPage(input) {
    var page = parseInt(input.value, 10);
    var max = parseInt(input.getAttribute('max'), 10);
    var min = parseInt(input.getAttribute('min'), 10) || 1;
    if (isNaN(page)) page = min;
    if (page < min) page = min;
    if (!isNaN(max) && page > max) page = max;
    var current = parseInt(input.dataset.currentPage || input.defaultValue, 10);
    if (page === current) return;
    var param = input.dataset.pageParam || 'page';
    var qs = input.dataset.paginationQs || '';
    var url = '?' + (qs ? qs + '&' : '') + param + '=' + page;
    window.location.href = url;
  }

  document.querySelectorAll('.app-pagination__page-input').forEach(function (input) {
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        goToPage(input);
      }
    });
    input.addEventListener('blur', function () {
      goToPage(input);
    });
  });
})();
