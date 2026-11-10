def reorderLinkedList(head):
    if not head:
        return None

    odd_head = odd_tail = even_head = even_tail = None
    count = 1
    current = head

    while current:
        if count % 2 == 0:  # Even index
            if not even_head:
                even_head = even_tail = current
            else:
                even_tail.next = current
                even_tail = even_tail.next
        else:  # Odd index
            if not odd_head:
                odd_head = odd_tail = current
            else:
                odd_tail.next = current
                odd_tail = odd_tail.next

        count += 1
        current = current.next

    if not odd_head:
        return even_head

    odd_tail.next = even_head
    if even_tail:
        even_tail.next = None

    return odd_head