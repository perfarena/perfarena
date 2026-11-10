def remove_elements(head, val):
    dummy = ListNode(0)
    dummy.next = head
    output = dummy
    
    while head is not None:
        if head.val != val:
            dummy.next = head
            dummy = dummy.next
        head = head.next
    
    dummy.next = None
    return output.next