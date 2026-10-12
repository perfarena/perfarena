class Node:
    def __init__(self, value=0, next=None):
        self.value = value
        self.next = next

def removeNodes(head, value):
    dummy = Node(0)
    dummy.next = head
    previous = dummy
    current = head
    
    while current:
        if current.value == value:
            previous.next = current.next
        else:
            previous = previous.next
        current = current.next
    
    return dummy.next