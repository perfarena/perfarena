def remove_elements(head, value):
    list = ListNode(0)
    list.next = head
    previous = list
    current = head
    
    while current is not None:
        if current.val == value:
            previous.next = current.next
        else:
            previous = current
        current = current.next
    
    return list.next