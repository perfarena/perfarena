function removeElements(head, Val) {
    if (head === null) {
        return null;
    }

    let currentNode = new ListNode(-1);
    currentNode.next = head;
    head = currentNode;

    while (currentNode.next !== null) {
        if (currentNode.next.val === Val) {
            currentNode.next = currentNode.next.next;
        } else {
            currentNode = currentNode.next;
        }
    }

    return head.next;
}