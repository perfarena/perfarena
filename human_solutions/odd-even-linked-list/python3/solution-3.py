def rearrange_linked_list(head):
    if not head or not head.next:
        return head

    current = head
    previous = None

    while current and current.next:
        if current.next:
            current.next, current.next.next, previous.next = current.next.next, previous.next, current.next
            previous = current
            current = current.next
        else:
            break

    return head