$(function() {
$('a#toggle-helper-tables').on('click', function(e) {
  e.preventDefault();
  $('ul#helper-tables').toggle();
});
$('input#table-search').on('keyup', function(e) {
  var searchQuery = $(this).val().toUpperCase();
  $('li.table-link').each(function() {
    var elem = $(this),
        tableName = elem.find('a').prop('innerText').toUpperCase();
    elem.toggle(tableName.indexOf(searchQuery) != -1);
  });
});
});
