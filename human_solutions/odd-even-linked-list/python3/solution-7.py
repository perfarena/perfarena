def odd_even_linked_list(head):
    if not head or not head.next:
        return head

    odd = head
    even = head.next
    even_head = head.next

    while even and even.next:
        odd.next = even.next
        odd = odd.next
        even.next = odd.next
        even = even.next

    odd.next = even_head

    return head