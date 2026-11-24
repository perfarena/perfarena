def oddEvenList(head):
    if not head:
        return head

    oddHead = head
    evenHead = head.next
    even = evenHead

    while even and even.next:
        oddHead.next = even.next
        oddHead = oddHead.next
        even.next = oddHead.next
        even = even.next

    oddHead.next = evenHead
    return head