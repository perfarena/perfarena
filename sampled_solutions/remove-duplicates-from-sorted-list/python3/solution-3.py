def remove_duplicates(head):
    current = head
    while current is not None:
        if current.next is not None and current.next.val == current.val:
            temp = current.val
            while current is not None and current.val == temp:
                current = current.next
            previous.next = current
        else:
            previous = current
            current = current.next
    return head