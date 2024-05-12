$(function() {

  $('table td').on('click', function() {
    $(this).attr('contenteditable', true).focus();
  });

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
