def reorderLinkedList(head):
    if not head or not head.next:
        return head

    old = head
    even = head.next
    evenHead = even

    while even and even.next:
        old.next = even.next
        old = old.next
        even.next = old.next
        even = even.next

    old.next = evenHead
    return head