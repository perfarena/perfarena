class ListNode:
    def __init__(self, x):
        self.val = x
        self.next = None

def removeElements(head, val):
    dummy = ListNode(0)
    dummy.next = head
    previous = dummy
    current = head

    while current is not None:
        if current.val == val:
            previous.next = current.next
        else:
            previous = current
        current = current.next

    return dummy.next