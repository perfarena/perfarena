# Create a ListNode class
class ListNode:
    def __init__(self, x):
        self.val = x
        self.next = None

def hasCycle(head):
    # Initialize a dictionary to store visited nodes
    dt = {}
    
    # Traverse through the linked list
    while head is not None:
        # If the node is already in the dictionary, return True (cycle detected)
        if head in dt:
            return True
        # Add the node to the dictionary
        dt[head] = True
        # Move to the next node
        head = head.next
    
    # If no cycle is found, return False
    return False