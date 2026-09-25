def used():
    return 1

def _private():
    return 2

class K:
    def method(self):
        return used()

def aliased():
    return 3
