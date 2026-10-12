def remove_duplicates(head):
    current = head
    while current is not None and current.next is not None:
        if current.next.val > current.val:
            current = current.next
        else:
            current.next = current.next.next
    return head