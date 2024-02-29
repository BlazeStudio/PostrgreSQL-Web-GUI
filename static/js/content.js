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

  // Обработчик клика по ячейке для изменения ее значения
  $('table td').on('click', function() {
    $(this).attr('contenteditable', true).focus();
  });

  // Обработчик события потери фокуса ячейкой, чтобы закончить редактирование
  $('table td').on('focusout', function() {
    $(this).removeAttr('contenteditable');
    // Получаем новое значение ячейки
    var newValue = $(this).text();
    // Получаем данные о столбце и строке
    var columnIndex = $(this).index();
    var rowIndex = $(this).parent().index();
    var columnLabel = $('thead th').eq(columnIndex).text();
    var rowLabel = $('tbody tr').eq(rowIndex).find('td').first().text();
    var table = $('#your_table').data('table');
    // Отправляем AJAX запрос только если значение изменилось
    if ($(this).data('originalValue') !== newValue) {
      $.ajax({
        type: 'POST',
        url: '/apply_changes',
        data: {
          table_name: table,
          columnLabel: columnLabel,
          rowLabel: rowLabel,
          newValue: newValue
        },
        success: function(response) {
          console.log('Данные успешно отправлены на сервер.');
        },
        error: function(error) {
          console.error('Произошла ошибка при отправке данных на сервер:', error);
        }
      });
    }
  });
});
