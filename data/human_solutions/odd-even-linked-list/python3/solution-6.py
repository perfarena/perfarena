def reorderLinkedList(head):
    if not head or not head.next:
        return head

    oddHead = odd = head
    evenHead = even = head.next

    while even and even.next:
        odd.next = even.next
        odd = odd.next
        even.next = odd.next
        even = even.next

    odd.next = evenHead

    return head