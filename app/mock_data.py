"""Offline replies for LLM_PROVIDER=mock, so the whole pipeline can be tested without API keys."""

CLOUD = {
    "topic": "IaaS vs PaaS vs SaaS",
    "pillar": "cloud",
    "cover": {"kicker": "CLOUD WEDNESDAY", "title": "IaaS vs PaaS vs **SaaS**",
              "subtitle": "Three cloud models, explained with one pizza."},
    "slides": [
        {"type": "point", "title": "Cloud = renting, not owning",
         "body": "Instead of buying servers, you rent computing from a provider like AWS, Azure or Google Cloud. "
                 "The only question is how much of the stack you manage yourself.",
         "bullets": [], "code": ""},
        {"type": "point", "title": "IaaS: you rent the hardware",
         "body": "The provider gives you virtual machines, storage and networks. You install and patch the OS, "
                 "runtime and apps.",
         "bullets": ["Examples: AWS EC2, Azure VMs", "Most control, most work"], "code": ""},
        {"type": "point", "title": "PaaS: you bring only code",
         "body": "The provider runs the servers, OS and runtime. You deploy your application and data.",
         "bullets": ["Examples: Heroku, Google App Engine", "Fast to ship, less control"],
         "code": "git push heroku main"},
        {"type": "point", "title": "SaaS: you just use it",
         "body": "The whole app is ready in your browser. The provider runs everything; you manage your "
                 "users and data.",
         "bullets": ["Examples: Gmail, Zoom, Slack"], "code": ""},
        {"type": "compare", "title": "Who manages what?",
         "columns": [{"name": "IaaS", "lines": ["You: OS + apps", "Them: hardware"]},
                     {"name": "PaaS", "lines": ["You: code + data", "Them: OS + runtime"]},
                     {"name": "SaaS", "lines": ["You: users + data", "Them: everything"]}]},
    ],
    "cta": {"title": "Save this cheat sheet", "subtitle": "Which one do you use at work? Tell us below."},
    "caption": "IaaS, PaaS and SaaS sound confusing, but the idea is simple.\n\n"
               "IaaS: you rent the servers and manage everything on top.\n"
               "PaaS: you bring your code, the platform runs it.\n"
               "SaaS: you just log in and use the app.\n\n"
               "Think of pizza. IaaS is renting a kitchen. PaaS is ordering a pizza base and adding your own "
               "toppings. SaaS is pizza delivered to your door.\n\n"
               "Network and system engineers moving to cloud usually start with IaaS, because it feels closest "
               "to the servers and VLANs they already know.\n\n"
               "Which one do you use the most at work or in your projects?",
    "hashtags": ["#cloudcomputing", "#aws", "#azure", "#devops", "#learntocode"],
    "alt_text": "A seven-slide carousel explaining IaaS, PaaS and SaaS with examples and a table of what the "
                "user and the provider manage in each model.",
    "sources": ["https://csrc.nist.gov/pubs/sp/800/145/final"],
    "claims_to_verify": ["AWS EC2 and Azure VMs are IaaS", "Heroku and Google App Engine are PaaS",
                         "Gmail, Zoom and Slack are SaaS"],
}


REEL = {
    "format": "reel",
    "topic": "Docker in 30 seconds",
    "pillar": "devops",
    "hook": {"kicker": "DEVOPS IN 30s", "title": "Docker in **30 seconds**",
             "say": "Your code works on your laptop but breaks on the server? Docker fixes that."},
    "scenes": [
        {"title": "Ship the whole box", "text": "Your app plus everything it needs, packed together.",
         "code": "", "say": "Docker packs your app together with its libraries and settings into one image."},
        {"title": "Run it anywhere", "text": "A container is a running copy of that image.",
         "code": "docker run -d -p 8080:80 nginx",
         "say": "Run the image and you get a container. This command starts a web server and maps port "
                "eighty eighty on your machine to port eighty inside."},
        {"title": "Lighter than a VM", "text": "Containers share the host kernel, so they start in seconds.",
         "code": "", "say": "Containers share the host's kernel instead of booting a full operating system, "
                            "so they are light and start fast."},
    ],
    "outro": {"title": "Save this for later", "say": "Follow for one byte of tech every day, and save this reel."},
    "caption": "Docker in 30 seconds.\n\nAn image is your app plus everything it needs. A container is a running "
               "copy of that image. Containers share the host kernel, so they start fast and use less memory "
               "than virtual machines.\n\nIf you come from networking, think of it like a standard config "
               "template you can drop on any switch.\n\nHave you run your first container yet?",
    "hashtags": ["#docker", "#devops", "#containers", "#learntocode"],
    "sources": ["https://docs.docker.com/get-started/docker-overview/"],
    "claims_to_verify": ["Containers share the host kernel", "docker run -p maps host ports to container ports"],
}


def reply(system, user, images):
    if "TASK: FACT_CHECK" in user:
        return {"verdict": "pass", "issues": []}
    if "TASK: VISION_CHECK" in user:
        return {"verdict": "pass", "issues": []}
    if "TASK: WRITE_REEL" in user or "TASK: REVISE_REEL" in user:
        return dict(REEL)
    if "TASK: NEWS_SUITABILITY" in user:
        return {"suitable": True, "reason": "mock: concrete facts and a clear lesson"}
    if "TASK: WEEKLY_REVIEW" in user:
        return {"patterns": ["Reels with a command on screen got the most saves (12.0 vs 3.1 per 1,000 reach)."],
                "experiments": [{"change": "Put the command in the hook", "keep": "Topic and length",
                                 "metric": "saves per 1,000 reach", "posts": "4 reels"}],
                "ideas": [{"pillar": "devops", "format": "reel", "topic": "5 kubectl commands for debugging"}],
                "missing_topics": [{"pillar": "cloud", "topic": "How to explain a home lab in an interview",
                                    "why": "Freshers have no job experience to talk about"}]}
    post = dict(CLOUD)
    for line in user.splitlines():
        if line.startswith("Topic: ") and "AUTO_NEWS" not in line:
            topic = line[7:].strip()
            if topic.lower() != CLOUD["topic"].lower():
                post = dict(CLOUD, topic=topic)
                post["cover"] = dict(CLOUD["cover"], title=topic[:40])
    return post
