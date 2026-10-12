public ListNode removeElements(ListNode head, int val) {
    ListNode dummy = new ListNode(0);
    dummy.next = head;
    ListNode previous = dummy;
    ListNode current = head;

    while (current != null) {
        if (current.val == val) {
            previous.next = current.next;
        } else {
            previous = current;
        }
        current = current.next;
    }

    return dummy.next;
}