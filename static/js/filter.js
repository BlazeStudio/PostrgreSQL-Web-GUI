$(function() {
  // Фильтрация таблицы по значениям столбцов
  $('.column-filter').on('input', function() {
    var columnIndex = $(this).data('column');
    var filterValue = $(this).val().toLowerCase();
    $('table tbody tr').each(function() {
      var cellValue = $(this).find('td').eq(columnIndex).text().toLowerCase();
      if (cellValue.includes(filterValue)) {
        $(this).show();
      } else {
        $(this).hide();
      }
    });
  });
  });