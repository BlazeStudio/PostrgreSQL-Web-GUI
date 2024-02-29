$(function() {
  $('a.view-sql').on('click', function(e) {
    e.preventDefault();
    var elem = $(this),
        pre = elem.siblings('div'),
        modalDiv = $('div#sql-modal');
    modalDiv.find('h4.modal-title').text(elem.data('name'));
    modalDiv.find('.modal-body').empty().append(pre.clone().show());
    modalDiv.modal();
  });
});
