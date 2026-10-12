def delete_duplicates(head):
    if head is None or head.next is None:
        return head
    
    if head.val == head.next.val:
        head.next = head.next.next
        delete_duplicates(head)
    
    return head