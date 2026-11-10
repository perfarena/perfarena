# Test the solution
head = ListNode(1)
head.next = ListNode(2)
head.next.next = ListNode(6)
head.next.next.next = ListNode(3)
head.next.next.next.next = ListNode(4)
head.next.next.next.next.next = ListNode(5)
head.next.next.next.next.next.next = ListNode(6)

new_head = removeElements(head, 6)

# Print the modified linked list
while new_head:
    print(new_head.val, end=" -> ")
    new_head = new_head.next