def rearrangeLinkedList(head):
    if head is None:
        return head

    odd = head
    even = odd.next
    even_head = even

    while even is not None and even.next is not None:
        odd.next = even.next
        odd = odd.next
        even.next = odd.next
        even = even.next

    odd.next = even_head

    return head