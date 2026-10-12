def rearrangeLinkedList(head):
    if not head or not head.next:
        return head

    odd_head = head
    even_head = head.next

    current_odd = odd_head
    current_even = even_head

    while current_odd and current_even and current_even.next:
        current_odd.next = current_even.next
        current_odd = current_odd.next

        current_even.next = current_odd.next
        current_even = current_even.next

    current_odd.next = even_head

    return odd_head