# Project Description

I have to build a managed sas kubernetes system, where customer will come a ask for give him cluster. It must have to be highly available.

like user will req to my api server, then it will pass to rmq and hit cluster luncher server. cluster luncher service will call aws api.

i will have a vpc(Control plane vpc) there will be a proxy server and there will be a subnet contain pg or mysql db and another subnet will contain master. all of master will lunch here and save to the db.

worker node will lunch on customer vpc and connect with master(using proxy) of your my vpc(Control plane vpc)

Master and worker will connect though a node.js cli. real time communication will happen using socker.io.

tech: React, Node, Express, aws, pulumi, ansible, rmq, socket.io, redis, grafana, promethous, Docker, kubernetes, otel, pgsql etc
